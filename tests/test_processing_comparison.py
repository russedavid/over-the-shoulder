import base64
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evals.processing_comparison import run
from evals.recording_data import write_json
from otsc.context import ContextStore
from otsc.models import Assistance
from otsc.privacy import private_write
from otsc.providers import latest_image


class ProcessingComparisonTests(unittest.TestCase):
    def test_both_modes_receive_the_same_permitted_image_and_context(self):
        seen = []

        class Provider:
            def __init__(self, choice, *, fast_mode):
                self.choice, self.fast_mode = choice, fast_mode
                self.last_raw_response = None
                self.last_raw_text = ""

            def generate(self, snapshot, lane, token, progress):
                seen.append((self.fast_mode, self.choice.reasoning, latest_image(snapshot, True), snapshot.prompt_context()))
                return Assistance(
                    task="Synthetic check", summary="Fixture response", conversation=[], artifacts=[],
                    observed_files=[], open_questions=[],
                )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            picture = root / "app" / "captures" / "fixture.png"
            # Valid PNG bytes are unnecessary here: the transport forwards bytes
            # and the stub does not decode or send them to any model.
            content = b"synthetic image transport fixture"
            private_write(picture, content)
            context = ContextStore()
            observation = context.add("screen", "Synthetic screen", "screen", image_path=str(picture))
            source = root / "source"
            write_json(source / "images" / f"{observation.id}.json", {"media": {"candidate": str(picture)}})
            write_json(source / "replays" / "session.json", {
                "session_id": context.session_id,
                "checkpoints": [{"id": "point", "input": context.snapshot().prompt_context()}],
            })
            with (
                patch.dict("os.environ", {"OTSC_DATA_DIR": str(root / "app")}),
                patch("evals.processing_comparison.CodexProvider", Provider),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                result = run(source, "point", root / "output")
            self.assertEqual([s[:2] for s in seen], [(False, "medium"), (True, "medium")])
            self.assertEqual(seen[0][2:], seen[1][2:])
            self.assertEqual(seen[0][2], base64.b64encode(content).decode())
            self.assertEqual(picture.read_bytes(), content)
            self.assertEqual((root / "output" / "input.png").read_bytes(), content)
            self.assertTrue(all(r["input_unchanged"] for r in result["results"]))
            seen.clear()
            with (
                patch.dict("os.environ", {"OTSC_DATA_DIR": str(root / "app")}),
                patch("evals.processing_comparison.CodexProvider", Provider),
                contextlib.redirect_stdout(io.StringIO()),
            ):
                run(source, "point", root / "reasoning", comparison="reasoning-fast")
            self.assertEqual([s[:2] for s in seen], [(True, "medium"), (True, "low")])
            self.assertEqual(seen[0][2:], seen[1][2:])
            self.assertIn("Astra Fast: medium vs low", (root / "reasoning" / "index.html").read_text())


if __name__ == "__main__":
    unittest.main()
