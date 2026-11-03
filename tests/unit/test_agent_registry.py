import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch, AsyncMock
from voice_agent.agents.registry import AgentRegistry
from voice_agent.agents.runtime import create_runtime
from voice_agent.agents.models import SessionSelection
from voice_agent.context.loader import ContextLoader


class AgentRegistryTests(unittest.TestCase):
    def test_voice_preferences_do_not_mutate_process_environment(self):
        import os
        from voice_agent.agents.models import VoicePreferences
        from voice_agent.providers.tts import create_tts
        from voice_agent.providers.azure_stt import StreamingAzureSTT
        with patch.dict(os.environ, {'DEFAULT_LANGUAGE': 'en', 'AZURE_STT_LANGUAGE': 'en-US'}):
            with patch('voice_agent.providers.azure_tts.StreamingAzureTTS') as tts:
                create_tts(language='ar', preferences=VoicePreferences(provider='azure', voice_id='ar-OM-demo'))
                tts.assert_called_once_with(timing=None, language='ar', voice_id='ar-OM-demo')
            stt = StreamingAzureSTT(timing=AsyncMock(), language='ar-OM')
            self.assertEqual(stt._language, 'ar-OM')
            self.assertEqual(os.environ['DEFAULT_LANGUAGE'], 'en')
            self.assertEqual(os.environ['AZURE_STT_LANGUAGE'], 'en-US')

    def test_bank_configuration_context_and_prompt_layers(self):
        runtime = create_runtime(SessionSelection(agent_id="bank_feedback", mock_customer_id="demo-ahmed"))
        self.assertEqual(runtime.manager.language, "ar")
        self.assertEqual(runtime.context.interaction.branch, "الخوير")
        self.assertEqual(runtime.context.call.call_id, runtime.manager.session_id)
        messages = runtime.manager.snapshot().messages
        self.assertIn("ROLE AND LANGUAGE", messages[0]["content"])
        self.assertEqual(json.loads(messages[1]["content"])["current_question"]["id"], "consent")
        self.assertIn("شركة داتامايننج", messages[2]["content"])
        self.assertEqual(messages[3]["role"], "user")
        self.assertNotIn("DEMO-BANK-001", messages[3]["content"])

    def test_missing_context_is_unknown_and_sessions_do_not_share_context(self):
        a = create_runtime(SessionSelection(agent_id="bank_feedback", mock_customer_id="demo-ahmed"))
        b = create_runtime(SessionSelection(agent_id="bank_feedback", mock_customer_id="demo-sara"))
        empty = create_runtime(SessionSelection(agent_id="bank_feedback"))
        self.assertNotEqual(a.context.customer.id, b.context.customer.id)
        self.assertNotEqual(a.manager.session_id, b.manager.session_id)
        self.assertIsNone(empty.context.customer)
        self.assertIsNone(empty.context.interaction)
        a.manager.answers["consent"] = "نعم"
        self.assertFalse(b.manager.answers)
        with self.assertRaises(ValueError):
            ContextLoader().load(a.config, "session", "demo-unknown")

    def test_unknown_agent_and_unauthorized_survey(self):
        with self.assertRaises(ValueError):
            AgentRegistry().get("missing")
        with self.assertRaises(ValueError):
            create_runtime(SessionSelection(agent_id="bank_feedback", survey_id="example"))

    def test_configuration_failures(self):
        base = dict(agent_id="sample", name="Sample", description="Demo", prompt_file="prompt.md",
                    workflow_type="survey", survey_id="example", tools=[], context=[])
        cases = [({"workflow_type": "unknown"}, True), ({"tools": ["missing"]}, True),
                 ({"survey_id": "missing"}, True), ({"prompt_file": "missing.md"}, True),
                 ({"prompt_file": "../escape.md"}, True), ({"name": ""}, True), ({}, False)]
        for changes, invalid in cases:
            with self.subTest(changes=changes), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                target = root / "sample"
                target.mkdir()
                (target / "agent.json").write_text(json.dumps({**base, **changes}), encoding="utf-8")
                (target / "prompt.md").write_text("Sample instructions", encoding="utf-8")
                if invalid:
                    with self.assertRaises((ValueError, OSError)):
                        AgentRegistry(root)
                else:
                    self.assertEqual(AgentRegistry(root).get("sample").config.agent_id, "sample")
