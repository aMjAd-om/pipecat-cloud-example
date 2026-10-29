"""Regression coverage for entry points and questionnaire relocation."""
import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from voice_agent.conversation import Questionnaire
from voice_agent.paths import PROJECT_ROOT


class LayoutTests(unittest.TestCase):
    def test_built_in_questionnaires_load_outside_repository(self):
        original = Path.cwd()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                current = Questionnaire.load("questionnaires/customer-satisfaction.ar.json")
                legacy = Questionnaire.load("questionnaire.customer-satisfaction.ar.json")
                self.assertEqual(current, legacy)
                self.assertEqual(len(current.questions), 9)
                self.assertTrue(Questionnaire.load("questionnaire.example.json").questions)
            finally:
                os.chdir(original)

    def test_custom_questionnaire_can_use_absolute_path(self):
        with tempfile.TemporaryDirectory() as directory:
            custom = Path(directory) / "custom.json"
            custom.write_bytes((PROJECT_ROOT / "questionnaires/example.json").read_bytes())
            self.assertTrue(Questionnaire.load(custom).questions)

    def test_package_entry_point_starts_service(self):
        with patch("voice_agent.api.app.run") as run:
            runpy.run_module("voice_agent", run_name="__main__")
        run.assert_called_once_with()
