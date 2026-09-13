import json
import tempfile
import unittest
from pathlib import Path

from evals.continuity import behavior_checks, render, summarize


def response(code):
    return {'artifacts': [{'kind': 'code', 'content': code}]}


class ContinuityEvaluationTests(unittest.TestCase):
    def test_boundary_mutations_cannot_pass_the_new_requirement(self):
        correct = response('def should_retry(status):\n    return status == 429 or 500 <= status <= 599\n')
        self.assertTrue(all(c['status'] == 'pass' for c in behavior_checks('changed_held', correct)))
        for code in ('return status >= 500', 'return 500 <= status <= 599',
                     'return status == 429 or status >= 499', 'return status == 429 or 500 <= status <= 600'):
            checks = behavior_checks('changed_held', response('def should_retry(status):\n    ' + code + '\n'))
            self.assertIn('fail', {c['status'] for c in checks})

    def test_unsupported_code_is_reviewable_and_never_executed(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'must-not-exist'
            code = f'open({str(target)!r}, "w").write("bad")\ndef should_retry(status):\n    return True\n'
            result = behavior_checks('initial', response(code))
            self.assertEqual({r['status'] for r in result}, {'needs_review'})
            self.assertFalse(target.exists())

    def test_unqualified_late_publication_is_not_counted_as_changed_task_success(self):
        rows = [{'event': 'terminal', 'stage': 'changed_held', 'qualified': False,
                 'published': True, 'lane': 'deep', 'type': 'result', 'response': {}},
                {'event': 'terminal', 'stage': 'changed_held', 'qualified': True,
                 'published': False, 'lane': 'deep', 'type': 'error', 'response': {}}]
        result = next(r for r in summarize(rows) if r['stage'] == 'changed_held')
        self.assertEqual(result['published_deep'], 0)
        self.assertEqual(result['errors'], 1)
        self.assertEqual(result['semantic_review'], 'needs_review')

    def test_read_only_review_escapes_outputs_and_does_not_write_human_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'calls').mkdir()
            (root / 'manifest.json').write_text('{}')
            event = {'event': 'terminal', 'stage': 'initial', 'seconds': 4.2, 'lane': 'deep',
                     'type': 'error', 'qualified': True, 'published': False,
                     'error': '<script>alert(1)</script>', 'response': {}}
            journal = json.dumps(event) + '\n'
            (root / 'timeline.jsonl').write_text(journal)
            render(root)
            self.assertIn('&lt;script&gt;', (root / 'index.html').read_text())
            self.assertNotIn('<script>', (root / 'index.html').read_text())
            self.assertEqual((root / 'timeline.jsonl').read_text(), journal)
            self.assertFalse((root / 'assistant-review.json').exists())
            self.assertFalse((root / 'human-review.json').exists())


if __name__ == '__main__':
    unittest.main()
