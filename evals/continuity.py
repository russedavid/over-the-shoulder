"""Read-only scoring and navigation for native continuity runs."""

import argparse
import html
import json
from pathlib import Path

from evals.checks import UnsupportedCode, run_function
from evals.continuity_cases import BEHAVIOR, RUBRIC, SCREENS, SPEECH, STAGES
from otsc.privacy import private_write


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
        checks = behavior_checks(stage, available[-1]['response']) if available else []
        rows.append({'stage': stage, 'qualified_deep_terminals': len(deep),
                     'published_deep': len(available),
                     'unchanged': sum(e.get('type') == 'unchanged' for e in deep),
                     'duplicates': sum(bool(e.get('duplicate')) for e in deep),
                     'errors': sum(e.get('type') == 'error' for e in deep),
                     'behavior': checks, 'semantic_review': 'needs_review',
                     'end': next((e for e in reversed(subset) if e['event'] == 'stage_finished'), None)})
    return rows


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
    if review.exists():
        body += detail('Assistant critique (not human labels)', json.loads(review.read_text()))
    body += '<p>Times are seconds since following started. A rendered fixture or complete PCM clip is the input boundary; held/unselected output is available but not automatically visible. Deliberately delayed stale completions are scheduling probes.</p>'
    for row in summary:
        stage = row['stage']
        body += f'<section id="{stage}"><h2>{esc(stage.replace("_", " "))}</h2><p>{esc(RUBRIC[stage])}</p>'
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
                body += '<p>' + esc(response.get('summary', e.get('reason', e.get('error', '')))) + '</p>'
                for reply in response.get('conversation', []):
                    body += '<p><strong>' + esc(reply['action']) + '</strong>: ' + esc(reply['text']) + '</p>'
                for a in response.get('artifacts', []):
                    body += detail(a['title'] + ' (' + a['kind'] + ')', a)
                body += detail('Snapshot, acceptance, plan, missing newer evidence and timing', e)
                if e.get('view'):
                    body += f'<a href="{e["view"]}"><img loading="lazy" src="{e["view"]}" alt="Native selected output"></a>'
            elif kind in {'hold', 'resume', 'stale_released', 'stage_finished', 'failure'}:
                body += detail(stamp + ' — ' + kind.replace('_', ' '), e)
        body += '</section>'
    body += '<h2>Every provider call</h2><p>Includes planning, context updates, OCR and answers; inputs and responses are retained independently of publication.</p>'
    for path in sorted((directory / 'calls').glob('*.json')):
        value = json.loads(path.read_text())
        target = path.with_suffix('.html')
        private_write(target, '<!doctype html><meta charset="utf-8"><a href="../index.html">Back to timeline</a>' + detail('Full request and response', value))
        body += f'<p><a href="calls/{target.name}">{esc(path.stem)}: {esc(value["lane"])} · {value.get("elapsed", 0):.2f}s · {esc(value.get("error", "completed"))}</a></p>'
    body += '<p><a href="timeline.jsonl">Full event journal</a> · <a href="objective-summary.json">Objective results JSON</a></p>'
    private_write(directory / 'index.html', '<!doctype html><meta charset="utf-8"><title>OTSC task continuity review</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:32px auto;padding:20px;background:#f6f0e5;color:#30291f}nav{position:sticky;top:0;background:#f6f0e5;padding:12px}section{border-top:2px solid #c6ae91;margin-top:36px}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#fffaf2;padding:14px}img{max-width:100%;max-height:650px}summary{cursor:pointer;padding:8px}a{color:#764723}audio{display:block}</style>' + body)
    return summary


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(render(args.run), indent=2))
