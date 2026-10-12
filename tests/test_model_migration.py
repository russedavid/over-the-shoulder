"""Saved retired models must not override the supported runtime default."""

import json
import tempfile
import unittest
from pathlib import Path

from otsc.codex import codex_command
from otsc.settings import Settings, load_settings, save_settings


class QuickModelMigrationTests(unittest.TestCase):
    def test_default_reaches_codex_with_none_reasoning_and_fast_processing(self):
        with tempfile.TemporaryDirectory() as temp:
            quick = load_settings(Path(temp)).quick
        command = codex_command("codex", Path("/tmp/work"), Path("/tmp/schema"), Path("/tmp/out"), quick)
        self.assertEqual(command[command.index("--model") + 1], "gpt-6-luna")
        self.assertIn('model_reasoning_effort="none"', command)
        self.assertIn('service_tier="fast"', command)

    def test_spark_migrates_including_old_app_saves_without_changing_other_settings(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for version in (None, 1, 2, 3):
                with self.subTest(version=version):
                    old = Settings(configured=True, microphone=True, transcription="local").model_dump()
                    old["quick"].update(model="gpt-5.3-codex-spark", reasoning="low", fast_mode=False, max_tokens=2400)
                    if version is None:
                        old.pop("model_defaults_version")
                    else:
                        old["model_defaults_version"] = version
                    for _ in range(2):
                        (root / "settings.json").write_text(json.dumps(old))
                        migrated = load_settings(root)
                        self.assertEqual(migrated.model_defaults_version, 3)
                        self.assertEqual(migrated.quick.model, "gpt-6-luna")
                        self.assertEqual(migrated.quick.reasoning, "none")
                        self.assertTrue(migrated.quick.fast_mode)
                        self.assertEqual(migrated.quick.max_tokens, 2400)
                        for key, value in old.items():
                            if key not in {"quick", "model_defaults_version"}:
                                self.assertEqual(migrated.model_dump()[key], value)
                        save_settings(migrated, root)
                        self.assertEqual(load_settings(root), migrated)

    def test_preserves_custom_models_disabled_lanes_and_other_providers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for provider, model in (("codex", "custom-model"), ("disabled", ""), ("compatible", "gpt-5.3-codex-spark")):
                with self.subTest(provider=provider, model=model):
                    data = Settings().model_dump()
                    data["model_defaults_version"] = 2
                    data["quick"].update(provider=provider, model=model, reasoning="low", fast_mode=False)
                    (root / "settings.json").write_text(json.dumps(data))
                    self.assertEqual(load_settings(root).quick.model_dump(), data["quick"])
