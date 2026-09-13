import json
import tempfile
import unittest
from pathlib import Path

from evals.continuity import available_code_type, behavior_checks, measure, render, summarize
from otsc.models import Artifact, Assistance
from otsc.output_browser import OutputBrowser
from otsc.output_history import OutputHistory


def response(code):
    return {'artifacts': [{'kind': 'code', 'content': code}]}


class ContinuityEvaluationTests(unittest.TestCase):
    def test_latency_excludes_an_old_snapshot_published_after_new_input(self):
        events = [{'event': 'stimulus', 'stage': 'participant_question', 'key': 'question', 'seconds': 10},
                  {'event': 'perception', 'stimulus': 'question', 'observations': [{'id': 'new'}], 'seconds': 11},
                  {'event': 'terminal', 'stage': 'participant_question', 'published': True, 'seconds': 12,
                   'lane': 'quick', 'snapshot': {'observations': [{'id': 'old'}]},
                   'selected_pane_changed': True, 'response': {'artifacts': []}},
                  {'event': 'terminal', 'stage': 'participant_question', 'published': True, 'seconds': 17,
                   'lane': 'quick', 'snapshot': {'observations': [{'id': 'new'}]},
                   'selected_pane_changed': False, 'response': {'artifacts': []}}]
        result = measure(events, [])['timings'][0]
        self.assertEqual(result['quick_available_seconds'], 7)
        self.assertIsNone(result['pane_change_seconds'])

    def test_unopened_code_can_be_selected_for_the_hold_probe(self):
        browser, history = OutputBrowser(), OutputHistory()
        early = Assistance(task='Example', summary='Quick guidance', conversation=[], artifacts=[],
                           observed_files=[], open_questions=[])
        browser.ingest(history.append(early, session_id='synthetic', goal='Example', lane='quick'))
        # User selected guidance before the code arrived: code must remain unopened.
        browser.select_type(browser.active_key)
        code = Artifact(id='code', kind='code', title='Code', content='x = 1', language='python', path='',
                        basis='example', source_ids=[], annotations=[{'line': 1, 'explanation': 'Assign one to x.'}], nodes=[], edges=[])
        later = early.model_copy(update={'artifacts': [code]})
        browser.ingest(history.append(later, session_id='synthetic', goal='Example', lane='deep'))
        key = available_code_type(browser)
        self.assertIsNone(browser.state.types[key].visible)
        self.assertTrue(browser.select_type(key))
        self.assertTrue(browser.frozen)
        self.assertEqual(browser.visible.artifact.content, 'x = 1')

    def test_no_change_uses_the_retained_answer_without_claiming_new_publication(self):
        prior = response('def should_retry(status):\n    return status == 429 or 500 <= status <= 599\n')
        rows = [{'event': 'terminal', 'stage': 'changed_held', 'qualified': True, 'accepted': True,
                 'published': False, 'lane': 'deep', 'type': 'unchanged',
                 'snapshot': {'previous_answer': prior}, 'response': {}}]
        result = next(r for r in summarize(rows) if r['stage'] == 'changed_held')
        self.assertEqual(result['published_deep'], 0)
        self.assertEqual(result['behavior_basis'], 'retained answer')
        self.assertTrue(all(c['status'] == 'pass' for c in result['behavior']))

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
            journal += json.dumps({**event, 'type': 'cancelled', 'error': None, 'reason': None}) + '\n'
            (root / 'timeline.jsonl').write_text(journal)
            render(root)
            self.assertIn('&lt;script&gt;', (root / 'index.html').read_text())
            self.assertNotIn('<script>', (root / 'index.html').read_text())
            self.assertEqual((root / 'timeline.jsonl').read_text(), journal)
            self.assertFalse((root / 'assistant-review.json').exists())
            self.assertFalse((root / 'human-review.json').exists())


if __name__ == '__main__':
    unittest.main()
