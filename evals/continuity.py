"""Read-only scoring and navigation for native continuity runs."""

import argparse
import collections
import html
import json
from pathlib import Path

from evals.checks import UnsupportedCode, run_function
from evals.continuity_cases import BEHAVIOR, RUBRIC, SCREENS, SPEECH, STAGES
from otsc.privacy import private_write


def available_code_type(browser):
    """A never-opened type has versions but intentionally has no visible cursor."""
    return next((key for key, stream in browser.state.types.items()
                 if stream.versions and stream.versions[-1].artifact
                 and stream.versions[-1].artifact.kind == 'code'), None)


def behavior_checks(stage, response):
    """A small behavioral check, never execution of model-produced Python."""
    examples = BEHAVIOR.get(stage, [])
    if not examples:
        return []
    code = [a['content'] for a in response.get('artifacts', []) if a['kind'] == 'code']
    if not code:
        return [{'status': 'needs_review', 'reason': 'No standalone code artifact available.'}]
    results = []
    for status, expected in examples:
        try:
            actual = run_function(code[0], 'should_retry', [status])
            results.append({'status': 'pass' if actual is expected else 'fail', 'input': status,
                            'expected': expected, 'actual': actual})
        except UnsupportedCode as error:
            results.append({'status': 'needs_review', 'input': status, 'reason': str(error)})
        except (ValueError, TypeError, SyntaxError, ZeroDivisionError) as error:
            results.append({'status': 'fail', 'input': status, 'reason': str(error)})
    return results


def stage_rows(events, stage):
    return [row for row in events if row.get('stage') == stage]


def summarize(events):
    rows = []
    for stage in STAGES:
        subset = stage_rows(events, stage)
        qualified = [e for e in subset if e['event'] == 'terminal' and e.get('qualified')]
        deep = [e for e in qualified if e.get('lane') == 'deep']
        available = [e for e in deep if e.get('published')]
        retained = next((e['snapshot']['previous_answer'] for e in reversed(deep)
                         if e.get('accepted') and e['type'] == 'unchanged'), None)
        assessed = available[-1]['response'] if available else retained
        checks = behavior_checks(stage, assessed) if assessed else []
        rows.append({'stage': stage, 'qualified_deep_terminals': len(deep),
                     'published_deep': len(available),
                     'unchanged': sum(e.get('type') == 'unchanged' for e in deep),
                     'duplicates': sum(bool(e.get('duplicate')) for e in deep),
                     'errors': sum(e.get('type') == 'error' for e in deep),
                     'behavior': checks, 'semantic_review': 'needs_review',
                     'behavior_basis': 'qualified publication' if available else 'retained answer' if retained else 'unavailable',
                     'end': next((e for e in reversed(subset) if e['event'] == 'stage_finished'), None)})
    return rows


def measure(events, calls):
    timings = []
    for stage, key in [('initial', 'initial_user'), ('changed_held', 'change'),
                       ('participant_question', 'question'), ('passive_pivot', 'pivot'),
                       ('superseded_task', 'new_task')]:
        stimulus = next((e for e in events if e['event'] == 'stimulus' and e.get('key') == key), None)
        if stimulus is None:
            continue
        matching = [e for e in events if e['event'] == 'perception' and e.get('stimulus') in {key, 'screen:' + key}]
        source_ids = {o['id'] for e in matching for o in e['observations']}
        eligible = [e for e in events if e['event'] == 'terminal' and e['stage'] == stage and e.get('published')
                    and source_ids.intersection(o['id'] for o in e['snapshot']['observations'])]
        def first(predicate):
            return next((round(e['seconds'] - stimulus['seconds'], 3) for e in eligible if predicate(e)), None)
        timings.append({'stage': stage, 'input': key,
                        'first_perception_seconds': round(matching[0]['seconds'] - stimulus['seconds'], 3) if matching else None,
                        'quick_available_seconds': first(lambda e: e['lane'] == 'quick'),
                        'deep_available_seconds': first(lambda e: e['lane'] == 'deep'),
                        'pane_change_seconds': first(lambda e: e['selected_pane_changed']),
                        'code_available_seconds': first(lambda e: any(a['kind'] == 'code' for a in e['response']['artifacts']))})
    return {'timings': timings, 'provider_calls': dict(collections.Counter(c['lane'] for c in calls)),
            'provider_errors': sum(bool(c.get('error')) for c in calls),
            'accepted_screen_readings': sum(e['event'] == 'perception' and e.get('type') == 'capture_done'
                                           and e.get('accepted', False) for e in events),
            'accepted_speech_clips': sum(e['event'] == 'perception' and e.get('type') == 'speech'
                                        and e.get('accepted', False) for e in events),
            'rejected_file_deltas': sum('Ignored file fragment not corroborated by visible source' in (e.get('notes') or [])
                                       for e in events),
            'stale_probes': [e for e in events if e['event'] == 'stale_released']}


def render(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text())
    events = [json.loads(line) for line in (directory / 'timeline.jsonl').read_text().splitlines()]
    summary = summarize(events)
    private_write(directory / 'objective-summary.json', json.dumps(summary, indent=2))
    esc = html.escape
    def detail(label, value):
        return '<details><summary>' + esc(label) + '</summary><pre>' + esc(json.dumps(value, indent=2)) + '</pre></details>'
    body = '<h1>OTSC: task continuity</h1><p>Real current runtime and models; synthetic screens and speech. No desktop or audio hardware capture. Semantic judgments remain provisional.</p>'
    body += '<nav>' + ' · '.join(f'<a href="#{stage}">{esc(stage.replace("_", " "))}</a>' for stage in STAGES) + '</nav>'
    body += detail('Run identity, models and measurement boundaries', manifest)
    review = directory / 'assistant-review.json'
    review_data = {}
    if review.exists():
        review_data = json.loads(review.read_text())
        for finding in review_data.get('findings', []):
            body += '<p><strong>' + esc(finding['title']) + '.</strong> ' + esc(finding['text']) + '</p>'
        body += detail('Full assistant critique (not human labels)', review_data)
    calls = [json.loads(p.read_text()) for p in sorted((directory / 'calls').glob('*.json'))]
    metrics = measure(events, calls)
    private_write(directory / 'review-metrics.json', json.dumps(metrics, indent=2))
    body += '<h2>Input to available output, seconds</h2><table><tr><th>Change</th><th>Perception</th><th>Quick</th><th>Deep</th><th>Selected pane change</th></tr>'
    for timing in metrics['timings']:
        body += '<tr>' + ''.join('<td>' + esc(str(timing[key]) if timing[key] is not None else 'not observed') + '</td>'
                                for key in ('stage', 'first_perception_seconds', 'quick_available_seconds', 'deep_available_seconds', 'pane_change_seconds')) + '</tr>'
    body += '</table>' + detail('All measured counts and timing boundaries', metrics)
    body += '<p>Times are seconds since following started. A rendered fixture or complete PCM clip is the input boundary; held/unselected output is available but not automatically visible. Deliberately delayed stale completions are scheduling probes.</p>'
    if (directory / 'presentation-replay/result.json').exists():
        body += '<h2>Held-code presentation replay</h2><p>Exact responses from this live run, replayed through the current native presentation without new inference. The original hold probe was invalid.</p>'
        body += detail('Nine native presentation checks', json.loads((directory / 'presentation-replay/result.json').read_text()))
        for filename, label in [('02-new-code-available-while-held.png', 'Original code held; one newer version available'),
                                ('03-latest.png', 'Latest selected; revised code visible')]:
            body += f'<p>{label}</p><img loading="lazy" src="presentation-replay/{filename}">'
    for row in summary:
        stage = row['stage']
        body += f'<section id="{stage}"><h2>{esc(stage.replace("_", " "))}</h2><p>{esc(RUBRIC[stage])}</p>'
        if stage in review_data.get('stages', {}):
            body += '<p><strong>Assistant review:</strong> ' + esc(review_data['stages'][stage]) + '</p>'
        body += detail('Objective results and completion status', row)
        subset = stage_rows(events, stage)
        for e in subset:
            stamp = f'{e["seconds"]:.2f}s'
            kind = e['event']
            if kind == 'stimulus':
                body += f'<h3>{stamp} — Input: {esc(e["kind"])}</h3>'
                if e['kind'] == 'screen':
                    body += f'<img loading="lazy" src="inputs/{e["key"]}.png"><pre>{esc(SCREENS[e["key"]])}</pre>'
                else:
                    body += f'<p>{esc(SPEECH[e["key"]][2])}</p><audio controls preload="none" src="inputs/{e["key"]}.wav"></audio>'
            elif kind == 'perception':
                body += detail(stamp + ' — Actual ' + e['type'], e)
            elif kind == 'terminal':
                body += f'<h3>{stamp} — {esc(e.get("lane", ""))}: {esc(e["type"])} · {"published" if e.get("published") else "not published"}</h3>'
                response = e.get('response', {})
                body += '<p>' + esc(response.get('summary') or e.get('reason') or e.get('error') or '') + '</p>'
                for reply in response.get('conversation', []):
                    body += '<p><strong>' + esc(reply['action']) + '</strong>: ' + esc(reply['text']) + '</p>'
                for a in response.get('artifacts', []):
                    body += detail(a['title'] + ' (' + a['kind'] + ')', a)
                body += detail('Snapshot, acceptance, plan, missing newer evidence and timing', e)
                if e.get('view'):
                    body += f'<a href="{e["view"]}"><img loading="lazy" src="{e["view"]}" alt="Native selected output"></a>'
            elif kind in {'hold', 'resume', 'stale_released', 'stage_finished', 'failure'}:
                body += detail(stamp + ' — ' + kind.replace('_', ' '), e)
            elif kind == 'memory' and (e.get('notes') or e.get('error') or any((e.get('update') or {}).values())):
                body += detail(stamp + ' — Working memory and source-validation notes', e)
        body += '</section>'
    body += '<h2>Every provider call</h2><p>Includes planning, context updates, OCR and answers; inputs and responses are retained independently of publication.</p>'
    for path in sorted((directory / 'calls').glob('*.json')):
        value = json.loads(path.read_text())
        target = path.with_suffix('.html')
        private_write(target, '<!doctype html><meta charset="utf-8"><a href="../index.html">Back to timeline</a>' + detail('Full request and response', value))
        body += f'<p><a href="calls/{target.name}">{esc(path.stem)}: {esc(value["lane"])} · {value.get("elapsed", 0):.2f}s · {esc(value.get("error") or "completed")}</a></p>'
    body += '<p><a href="timeline.jsonl">Full event journal</a> · <a href="objective-summary.json">Objective results JSON</a></p>'
    private_write(directory / 'index.html', '<!doctype html><meta charset="utf-8"><title>OTSC task continuity review</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:20px;background:#f6f0e5;color:#30291f}nav{position:sticky;top:0;background:#f6f0e5;padding:12px}section{border-top:2px solid #c6ae91;margin-top:36px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fffaf2;padding:14px}img{max-width:100%;max-height:650px}summary{cursor:pointer;padding:8px}a{color:#764723}audio{display:block}</style>' + body)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.run), indent=2))
