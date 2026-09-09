import tempfile
import unittest
from pathlib import Path

from evals.anchors import anchors
from evals.cases import make_snapshot
from evals.checks import UnsupportedCode, run_function, task_checks
from evals.report import write_report
from evals.runner import outcome, validate_corpus


class EvaluationTests(unittest.TestCase):
    def test_corpus_has_disjoint_scenario_families_and_no_reference_leakage(self):
        manifest = validate_corpus()
        self.assertEqual(manifest["cases"], 28)
        self.assertEqual((manifest["development"], manifest["holdout"]), (20, 8))
        self.assertFalse(manifest["human_calibrated"])

    def test_restricted_checker_distinguishes_missing_zero_and_numeric_bounds(self):
        code = "def mean(values):\n    if not values:\n        return None\n    return sum(values) / len(values)\n"
        self.assertIsNone(run_function(code, "mean", [[]]))
        self.assertEqual(run_function(code, "mean", [[0]]), 0)
        self.assertFalse(
            run_function("def retry(status):\n    return status == 429 or 500 <= status <= 599\n", "retry", [600])
        )

    def test_model_code_cannot_execute_io_imports_or_unbounded_work(self):
        for code in [
            "def bad():\n    return open('/etc/passwd').read()\n",
            "import os\ndef bad():\n    return 1\n",
            "def bad():\n    return 10 ** (10 ** 10)\n",
            "def bad():\n    raise ValueError(open('/etc/passwd'))\n",
        ]:
            with self.subTest(code=code), self.assertRaises(UnsupportedCode):
                run_function(code, "bad", [])

    def test_bad_reference_code_fails_even_if_a_judge_were_to_pass_it(self):
        item = next(a for a in anchors() if a["id"] == "bounds-01-negative")
        checks = task_checks(item["case"], item["response"], make_snapshot(item["case"]))
        self.assertFalse(outcome(checks, {"passed": True}))
        self.assertIsNone(outcome([{"passed": True}], None))

    def test_report_escapes_adversarial_text(self):
        run = {
            "run_id": "example",
            "mode": "standard",
            "corpus": {"version": "1"},
            "summary": {},
            "manifest": {},
            "results": [
                {"case_id": "example", "family": "security", "goal": "<script>unsafe()</script>", "task_passed": False}
            ],
        }
        with tempfile.TemporaryDirectory() as directory:
            path = write_report(run, Path(directory))
            content = path.read_text()
            self.assertIn("&lt;script&gt;", content)
            self.assertNotIn("<script>unsafe", content)


if __name__ == "__main__":
    unittest.main()
