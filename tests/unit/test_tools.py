import asyncio
import json
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock
from voice_agent.agents.models import SessionSelection
from voice_agent.agents.runtime import create_runtime
from voice_agent.tools.registry import default_tools, ToolRegistry
from voice_agent.tools.definitions.visits import VisitArguments
from voice_agent.tools.executor import ToolExecutor
from voice_agent.conversation.models import DECISION_FUNCTION_NAME
from voice_agent.providers.decision_protocol import DECISION_FIELDS
from tests.unit.test_survey import decision


def response(*, payload=None, tool=None, arguments='{"limit": 3}'):
    if payload is not None:
        tool, arguments = DECISION_FUNCTION_NAME, json.dumps(
            {short: payload[full] for short, full in DECISION_FIELDS.items()})
    calls = [NS(id="call-1", function=NS(name=tool, arguments=arguments))] if tool else None
    return NS(choices=[NS(finish_reason="tool_calls" if tool else "stop",
        message=NS(content=json.dumps(payload) if payload else None, tool_calls=calls, refusal=None))],
        usage=NS(model_dump=lambda: {"total_tokens": 10}))


class ToolTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, customer="demo-ahmed"):
        return create_runtime(SessionSelection(agent_id="bank_feedback", mock_customer_id=customer))

    def executor(self, runtime, **changes):
        return ToolExecutor(default_tools(runtime.repository), agent_id=runtime.config.agent_id,
                            allowed=runtime.config.tools, context=runtime.context, **changes)

    async def test_permissions_arguments_and_trusted_customer_isolation(self):
        a, b = self.runtime(), self.runtime("demo-sara")
        executor = self.executor(a)
        result = await executor.execute("get_customer_visits", '{"limit": 5}')
        self.assertTrue(result["ok"])
        self.assertEqual(len(result["data"]["visits"]), 2)
        other = await self.executor(b).execute("get_customer_visits", '{"limit": 5}')
        self.assertNotIn("DEMO-VIS-1025", json.dumps(other))
        for args in ('{"limit": 0}', '{"limit": "2"}', '{"limit": 2, "customer_id": "DEMO-BANK-002"}', 'null'):
            self.assertEqual((await executor.execute("get_customer_visits", args))["error"], "invalid_arguments")
        self.assertEqual((await executor.execute("exec", '{}'))["error"], "tool_not_allowed")
        denied = ToolExecutor(default_tools(), agent_id="other", allowed=(), context=a.context)
        self.assertFalse((await denied.execute("get_customer_visits", '{"limit": 1}'))["ok"])
        mismatched = ToolExecutor(default_tools(), agent_id="other", allowed=a.config.tools, context=a.context)
        self.assertEqual((await mismatched.execute("get_customer_visits", '{"limit": 1}'))["error"], "context_unavailable")

    async def test_failure_timeout_and_schema_registration(self):
        async def fail(context, arguments):
            raise RuntimeError("private information must not be returned")
        async def slow(context, arguments):
            await asyncio.sleep(1)
        for handler, error in ((fail, "tool_failed"), (slow, "tool_timeout")):
            registry = ToolRegistry()
            registry.register("demo", "Demo", VisitArguments, handler)
            with self.assertRaises(ValueError):
                registry.register("demo", "Duplicate", VisitArguments, handler)
            executor = ToolExecutor(registry, agent_id="bank_feedback", allowed=("demo",),
                                    context=self.runtime().context, timeout=.01)
            result = await executor.execute("demo", '{"limit": 1}')
            self.assertEqual(result, {"ok": False, "error": error})
            self.assertNotIn("private", json.dumps(list(executor.events)))

    async def test_provider_tool_roundtrip_preserves_state_and_structured_contract(self):
        runtime = self.runtime()
        client = NS(chat=NS(completions=NS(create=AsyncMock(side_effect=[
            response(tool="get_customer_visits"), response(payload=decision("consent", intent="company",
                answer_status="not_answered", answer_value=None, action="stay",
                spoken_response="حسب بيانات التجربة، عندك زيارة سابقة لفرع نزوى."))]))))
        decide = runtime.decision_client(client, "mock")
        result = await runtime.manager.turn("history", "وين كانت زيارتي اللي قبلها؟", decide)
        self.assertTrue(result.accepted)
        self.assertFalse(result.committed)
        self.assertEqual(runtime.manager.question.id, "consent")
        self.assertFalse(runtime.manager.answers)
        self.assertEqual(client.chat.completions.create.await_count, 2)
        call = client.chat.completions.create.call_args_list[-1].kwargs
        self.assertEqual(call["messages"][-1]["role"], "tool")
        self.assertNotIn("response_format", call)
        final_schema = next(t["function"] for t in call["tools"] if t["function"]["name"] == DECISION_FUNCTION_NAME)
        self.assertTrue(final_schema["strict"])
        self.assertNotIn("current_question_id", final_schema["parameters"]["properties"])
        self.assertEqual(set(final_schema["parameters"]["required"]), {
            "intent", "status", "value", "action", "reply"})
        self.assertEqual(runtime.manager.metrics[-1]["tool_calls"], 1)
        # Retrieved facts remain available on the next turn without another lookup.
        client.chat.completions.create.side_effect = None
        client.chat.completions.create.return_value = response(payload=decision('consent',
            intent='company', answer_status='not_answered', answer_value=None, action='stay',
            spoken_response='حسب بيانات التجربة، كانت في فرع نزوى.'))
        await runtime.manager.turn('followup', 'أي فرع قلت؟', decide)
        self.assertTrue(any('RETRIEVED_TOOL_DATA' in str(m.get('content', ''))
            for m in client.chat.completions.create.call_args.kwargs['messages']))
        self.assertEqual(runtime.manager.metrics[-1]['tool_calls'], 0)

    async def test_simple_reply_needs_one_model_call_and_no_tool(self):
        runtime = self.runtime()
        client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=response(
            payload=decision("consent", intent="company", answer_status="not_answered",
                answer_value=None, action="stay", spoken_response="من شركة داتامايننج."))))))
        decide = runtime.decision_client(client, "mock")
        result = await runtime.manager.turn("company", "من أي شركة؟", decide)
        self.assertTrue(result.accepted)
        self.assertEqual(client.chat.completions.create.await_count, 1)
        self.assertEqual(runtime.manager.metrics[-1]["tool_calls"], 0)
        options = client.chat.completions.create.call_args.kwargs
        self.assertEqual(options['tool_choice'], 'required')
        self.assertFalse(options['parallel_tool_calls'])
        self.assertNotIn('response_format', options)
        self.assertFalse(decide.executor.events, 'The decision output has no executable handler')

    async def test_tool_enabled_free_text_or_multiple_decisions_cannot_change_state(self):
        runtime = self.runtime()
        payload = decision('consent', answer_value='نعم')
        raw = json.dumps(payload) + '\n' + json.dumps(payload)
        malformed = NS(choices=[NS(finish_reason='stop', message=NS(
            content=raw, tool_calls=None, refusal=None))], usage=None)
        client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=malformed))))
        result = await runtime.manager.turn('invalid', 'نعم', runtime.decision_client(client, 'mock'))
        self.assertFalse(result.accepted)
        self.assertFalse(runtime.manager.answers)
        self.assertEqual(runtime.manager.question.id, 'consent')

    async def test_final_decision_schema_does_not_bypass_state_validation(self):
        runtime = self.runtime()
        invalid = response(payload=decision('consent', answer_value=4))
        client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=invalid))))
        result = await runtime.manager.turn('invalid-consent', 'أربعة', runtime.decision_client(client, 'mock'))
        self.assertFalse(result.accepted)
        self.assertFalse(runtime.manager.answers)
        self.assertEqual(runtime.manager.question.id, 'consent')

    async def test_output_function_name_is_reserved(self):
        async def handler(context, arguments):
            return {}
        with self.assertRaises(ValueError):
            ToolRegistry().register(DECISION_FUNCTION_NAME, 'No executable output tool', VisitArguments, handler)

    async def test_tool_failure_and_iteration_limit_return_safe_fallback(self):
        for tool in ("unknown_tool", "get_customer_visits"):
            runtime = self.runtime()
            client = NS(chat=NS(completions=NS(create=AsyncMock(return_value=response(tool=tool)))))
            decide = runtime.decision_client(client, "mock")
            result = await runtime.manager.turn("history", "أبي الزيارات السابقة", decide)
            self.assertTrue(result.accepted)
            self.assertIn("ما قدرت", result.spoken_response)
            self.assertFalse(runtime.manager.answers)
            self.assertLessEqual(client.chat.completions.create.await_count, 3)

    async def test_interruption_during_tool_request_prevents_execution(self):
        runtime = self.runtime()
        async def interrupted(**options):
            runtime.manager.interrupt()
            return response(tool="get_customer_visits")
        client = NS(chat=NS(completions=NS(create=AsyncMock(side_effect=interrupted))))
        decide = runtime.decision_client(client, "mock")
        result = await runtime.manager.turn("history", "أي زيارة؟", decide)
        self.assertEqual(result.reason, "stale")
        self.assertFalse(decide.executor.events)
