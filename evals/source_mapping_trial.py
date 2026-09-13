"""Explicit current-component replay of the continuity study's source-mapping fault."""

import argparse
import html
import json
import os
import shutil
import time
from pathlib import Path

from PIL import Image

from evals.continuity import behavior_checks
from otsc.capture import ScreenCapture
from otsc.codex import CodexProvider
from otsc.context import ContextStore
from otsc.context_builder import ContextBuilder
from otsc.models import Assistance, Observation, ObservedFile
from otsc.perception import read_screen
from otsc.planning import TaskPlanningProvider
from otsc.privacy import private_write
from otsc.scheduler import Cancellation
from otsc.settings import load_settings
from otsc.telemetry import digest, release_manifest


def restore_observation(raw):
    if raw.get('reading'):
        raw = {**raw, 'reading': {**raw['reading'], 'visible_text': raw['text']}}
    return Observation.model_validate(raw)


def run(source, output):
    root = Path(__file__).resolve().parents[1]
    if output == root or root in output.parents:
        raise ValueError('Keep generated results outside Git')
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    settings = load_settings()
    choices = {key: getattr(settings, key) for key in ('ocr', 'context_builder', 'planner', 'deep')}
    if any(c.provider != 'codex' for c in choices.values()):
        raise ValueError('This comparison requires the configured Codex providers; no automatic fallback')
    os.environ['OTSC_DATA_DIR'] = str(output / 'private-app-state')
    timeline = [json.loads(line) for line in (source / 'timeline.jsonl').read_text().splitlines()]
    original = {o['id']: (restore_observation(o), row['stimulus']) for row in timeline
                if row['event'] == 'perception' for o in row['observations']}
    report = {'source_run': str(source), 'source_journal_hash': digest((source / 'timeline.jsonl').read_text()),
              'release': release_manifest(), 'models': {k: c.model_dump() for k, c in choices.items()},
              'hardware_capture': False, 'new_audio_transcription': False, 'human_calibrated': False,
              'cases': [], 'replayed_file_candidates': [], 'calls': []}

    class RecordedProvider(CodexProvider):
        def generate_json(self, snapshot, lane, token, progress, **kwargs):
            began = time.monotonic()
            row = {'lane': lane, 'model': self.choice.model_dump(), 'snapshot': snapshot.prompt_context(), 'request': kwargs}
            try:
                result = super().generate_json(snapshot, lane, token, progress, **kwargs)
                row['response'] = result
                return result
            except Exception as error:
                row['error'] = str(error)
                raise
            finally:
                row['elapsed'] = time.monotonic() - began
                row['raw_text'] = self.last_raw_text
                report['calls'].append(row)
                private_write(output / 'report.json', json.dumps(report, indent=2))
                print(json.dumps({'lane': lane, 'seconds': round(row['elapsed'], 2), 'error': row.get('error')}), flush=True)

    fresh_sources = {}
    for key in ('initial', 'changed'):
        row = {'key': key}
        report['cases'].append(row)
        scanner = ScreenCapture()
        context = ContextStore()
        builder = ContextBuilder(context, lambda: RecordedProvider(choices['context_builder']))
        try:
            shutil.copy2(source / 'inputs' / (key + '.png'), output / (key + '.png'))
            _, path = scanner.interpret(Image.open(output / (key + '.png')).convert('RGB'), keep_image=True, full_resolution=True)
            began = time.monotonic()
            reading = read_screen(path, RecordedProvider(choices['ocr']), Cancellation(), lambda text: None)
            row['ocr_seconds'] = time.monotonic() - began
            row['reading'] = reading.model_dump()
            observation = context.add('screen', reading.visible_text, 'screen', image_path=path, reading=reading,
                                      confidence='uncertain')
            fresh_sources['screen:' + key] = observation
            wanted = {'initial_other', 'initial_user'} | ({'change'} if key == 'changed' else set())
            for old, stimulus in original.values():
                if stimulus in wanted:
                    context.add('speech', old.text, old.channel, old.speaker, confidence='transcribed')
            builder.request()
            event = builder.events.get(timeout=260)
            builder.accept(event)
            row['context_update'] = event['update'].model_dump() if 'update' in event else None
            row['context_error'] = event.get('error')
            row['context_notes'] = event.get('notes')
            row['workspace'] = json.loads(context.workspace_text())
            snapshot = context.snapshot()
            row['answer_snapshot'] = snapshot.prompt_context()
            provider = TaskPlanningProvider(RecordedProvider(choices['deep']), RecordedProvider(choices['planner']))
            response = provider.generate(snapshot, 'deep', Cancellation(), lambda text: None)
            row['response'] = response.model_dump()
            row['delivery_notes'] = response._delivery_notes
            row['plan'] = response._task_plan
            row['behavior'] = behavior_checks('initial' if key == 'initial' else 'changed_held', row['response'])
            row['diffs'] = [a.model_dump() for a in response.artifacts if a.kind == 'patch']
        except Exception as error:
            row['error'] = str(error)
        finally:
            builder.close()
            scanner.close()
            private_write(output / 'report.json', json.dumps(report, indent=2))

    # These are the unchanged proposed file bytes from the original failed
    # context updates. Only their citations are linked to a fresh reading of
    # the same fixture image. This is not 21 new context-model generations.
    for event in timeline:
        if event['event'] != 'memory' or 'Ignored file fragment not corroborated by visible source' not in (event.get('notes') or []):
            continue
        for raw in event['update']['observed_files']:
            item = ObservedFile.model_validate(raw)
            old_sources = [original[s][0] for s in item.source_ids]
            new_sources = [fresh_sources[original[s][1]] for s in item.source_ids if original[s][1] in fresh_sources]
            record = {'at': event['seconds'], 'original': raw, 'content_hash': digest(item.content),
                      'fresh_source_ids': [s.id for s in new_sources]}
            probe = Assistance(task='', summary='', conversation=[], artifacts=[], open_questions=[], observed_files=[item])
            for label, sources in [('before', old_sources), ('after', new_sources)]:
                if len(sources) != len(item.source_ids):
                    record[label] = 'not_assessed'
                    record[label + '_reason'] = 'Fresh perception unavailable; see the recorded provider failure.'
                    continue
                candidate = item.model_copy(update={'source_ids': [s.id for s in sources]})
                probe.observed_files = [candidate]
                try:
                    probe.validate_sources(sources)
                    record[label] = 'accepted'
                except ValueError as error:
                    record[label] = 'rejected'
                    record[label + '_reason'] = str(error)
            report['replayed_file_candidates'].append(record)
    report['runtime_source_unchanged'] = release_manifest()['code_hash'] == report['release']['code_hash']
    report['source_journal_unchanged'] = digest((source / 'timeline.jsonl').read_text()) == report['source_journal_hash']
    private_write(output / 'report.json', json.dumps(report, indent=2))
    body = '<h1>OCR source mapping: current-component replay</h1><p>Same synthetic screenshots; fresh OCR, context building, planning and deep assistance. No hardware capture or new ASR. Original results remain unchanged.</p>'
    for case in report['cases']:
        body += '<h2>' + case['key'] + '</h2><img width="900" src="' + case['key'] + '.png">'
        body += '<h3>Mapped OCR</h3><pre>' + html.escape(json.dumps(case.get('reading', {}).get('code_blocks'), indent=2)) + '</pre>'
        for diff in case.get('diffs', []):
            body += '<h3>Delivered source-backed diff</h3><pre>' + html.escape(diff['content']) + '</pre>'
        body += '<details><summary>Full source, workspace, response, checks and plan</summary><pre>' + html.escape(json.dumps(case, indent=2)) + '</pre></details>'
    recovered = sum(r['after'] == 'accepted' for r in report['replayed_file_candidates'])
    body += f'<h2>Original file candidates</h2><p>{recovered}/{len(report["replayed_file_candidates"])} accepted against fresh perception of the same screenshots. Candidate code and line origins are unchanged; citations are explicitly relinked.</p>'
    body += '<details><summary>Every before/after candidate</summary><pre>' + html.escape(json.dumps(report['replayed_file_candidates'], indent=2)) + '</pre></details><p><a href="report.json">All model requests, outputs and run identity</a></p>'
    private_write(output / 'index.html', '<!doctype html><meta charset="utf-8"><title>OTSC source mapping review</title><style>body{font:16px/1.5 system-ui;max-width:1100px;margin:30px auto;padding:20px;background:#f6f0e5;color:#30291f}img{max-width:100%}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:white;padding:16px}summary{cursor:pointer}</style>' + body)
    print(json.dumps({'review': str(output / 'index.html'), 'file_candidates_recovered': recovered, 'calls': len(report['calls'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    run(args.source_run.resolve(), args.output.resolve())
