"""Explicit finite native evaluation; never imported by ordinary app startup.

Run with .venv/bin/python -m evals.native_continuity --output /tmp/otsc-continuity-RUN
"""

import argparse
import json
import os
import subprocess
import threading
import time
import traceback
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import AppKit as A
import numpy as np
import objc
from Foundation import NSObject, NSTimer
from PIL import Image, ImageDraw, ImageFont
from PyObjCTools import AppHelper

from evals.continuity import render
from evals.continuity_cases import RUBRIC, SCREENS, SPEECH, STAGES
from otsc.app import Controller
from otsc.codex import CodexProvider
from otsc.privacy import private_directory, private_write, redact
from otsc.scheduler import Coordinator
from otsc.settings import Settings, load_settings
from otsc.telemetry import digest, release_manifest


def prepare(directory, settings):
    root = Path(__file__).resolve().parents[1]
    if directory == root or root in directory.parents:
        raise ValueError('Keep generated evaluation output outside the source checkout')
    directory.mkdir(mode=0o700, parents=True, exist_ok=False)
    private_directory(directory / 'inputs')
    private_directory(directory / 'calls')
    private_directory(directory / 'views')
    font = ImageFont.truetype('/System/Library/Fonts/Menlo.ttc', 25)
    for name, text in SCREENS.items():
        image = Image.new('RGB', (1600, 850), '#fffaf2')
        ImageDraw.Draw(image).multiline_text((35, 35), text, font=font, fill='#30291f', spacing=15)
        image.save(directory / 'inputs' / (name + '.png'))
    for key, (_, voice, sentence) in SPEECH.items():
        subprocess.run(['say', '-v', voice, '-o', str(directory / 'inputs' / (key + '.wav')),
                        '--data-format=LEI16@16000', sentence], check=True, timeout=30)
    manifest = {'release': release_manifest(), 'settings': settings.model_dump(),
                'synthetic_inputs': True, 'hardware_capture': False, 'audible_playback': False,
                'human_labels': False, 'completed': False, 'stages': STAGES, 'rubric': RUBRIC,
                'input_hashes': {p.name: digest(p.read_bytes().hex()) for p in sorted((directory / 'inputs').iterdir())},
                'evaluator_hashes': {p: digest(Path(p).read_text()) for p in
                                     ('evals/native_continuity.py', 'evals/continuity.py', 'evals/continuity_cases.py',
                                      'evals/continuity-protocol.md')},
                'timing_boundary': 'Complete fixture available to selected native view display flush; not acoustic or physical pixel timing.',
                'limits': {'seconds': 1200, 'provider_calls': 120, 'stage_seconds': 240}}
    private_write(directory / 'manifest.json', json.dumps(manifest, indent=2))
    return manifest


class NativeContinuity(NSObject):
    def initWithDirectory_settings_manifest_(self, directory, settings, manifest):
        self = objc.super(NativeContinuity, self).init()
        self.directory, self.settings, self.manifest = Path(directory), settings, manifest
        self.lock, self.call_lock = threading.Lock(), threading.Lock()
        self.rows, self.calls, self.frames, self.accepted = [], 0, {}, {}
        self.sources, self.speech_times = {}, {}
        self.stage, self.screen_key = 'initial', 'initial'
        self.started = self.stage_started = time.monotonic()
        self.finished, self.held_event, self.stale_phase = False, None, ''
        self.terminals, self.screen_completions = [], 0
        self.hold_state = None
        self.patches = []
        original_json = CodexProvider.generate_json
        original_handle = Controller.handle_event
        original_accept = Coordinator.accept

        def generate(provider, snapshot, lane, token, progress, **kwargs):
            with self.call_lock:
                self.calls += 1
                number = self.calls
            if number > 120:
                raise RuntimeError('Evaluation provider-call ceiling reached')
            started = time.monotonic()
            record = {'number': number, 'stage': self.stage, 'lane': lane,
                      'started': started - self.started, 'model': provider.choice.model_dump(),
                      'snapshot': snapshot.prompt_context(), 'session_id': snapshot.session_id,
                      'task_revision': snapshot.task_revision, 'request': kwargs}
            self.emit('call_started', call=number, lane=lane)
            try:
                result = original_json(provider, snapshot, lane, token, progress, **kwargs)
                record['response'] = result
                return result
            except Exception as error:
                record['error'] = redact(str(error))[:1000]
                raise
            finally:
                record['elapsed'] = time.monotonic() - started
                record['raw_text'] = provider.last_raw_text
                private_write(self.directory / 'calls' / f'{number:03d}.json', json.dumps(record, indent=2))
                self.emit('call_finished', call=number, lane=lane, error=record.get('error'), elapsed=record['elapsed'])

        def accept(coordinator, event):
            result = original_accept(coordinator, event)
            self.accepted[id(event)] = result
            return result

        def handle(controller, event):
            if (self.stage == 'superseded_task' and self.stale_phase == 'waiting_old'
                    and event['type'] == 'result' and event['job'].lane == 'deep'
                    and not event['job'].snapshot.automatic_refresh):
                self.held_event = event
                self.stale_phase = 'ready_to_switch'
                self.emit('completion_delayed', request_id=event['job'].request_id,
                          snapshot=event['job'].snapshot.prompt_context())
                return
            before = self.view_state()
            original_handle(controller, event)
            self.observe(event, before)

        for target, replacement in [('otsc.codex.CodexProvider.generate_json', generate),
                                    ('otsc.scheduler.Coordinator.accept', accept),
                                    ('otsc.app.Controller.handle_event', handle),
                                    ('pyautogui.screenshot', self.fixture_frame),
                                    ('otsc.app.load_settings', lambda: settings)]:
            monkey = patch(target, replacement)
            monkey.start()
            self.patches.append(monkey)
        self.controller = Controller.alloc().initWithOptions_(SimpleNamespace(
            demo=False, design_demo=False, smoke_test=None, disable_midi=True, trace_source='synthetic-native-continuity'))
        self.started = self.stage_started = time.monotonic()
        self.emit('stage_started')
        self.emit('stimulus', kind='screen', key='initial')
        self.controller.toggleRunning_(None)
        self.speak('initial_other')
        self.speak('initial_user')
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(.1, self, 'tick:', None, True)
        return self

    @objc.python_method
    def emit(self, event, **values):
        row = {'event': event, 'stage': self.stage, 'seconds': round(time.monotonic() - self.started, 4), **values}
        with self.lock:
            self.rows.append(row)
            with (self.directory / 'timeline.jsonl').open('a') as stream:
                stream.write(json.dumps(row) + '\n')
        if event in {'stage_started', 'stage_finished', 'failure', 'stale_released'}:
            print(json.dumps(row), flush=True)
        return row

    @objc.python_method
    def fixture_frame(self, **kwargs):
        key = self.screen_key
        self.frames[self.controller.capture_id] = key
        self.emit('frame_acquired', key=key, capture_id=self.controller.capture_id)
        return Image.open(self.directory / 'inputs' / (key + '.png')).convert('RGB')

    @objc.python_method
    def speak(self, key):
        channel = SPEECH[key][0]
        with wave.open(str(self.directory / 'inputs' / (key + '.wav')), 'rb') as audio:
            samples = np.frombuffer(audio.readframes(audio.getnframes()), dtype='<i2').astype(np.float32) / 32768
        at = time.time()
        self.speech_times[at] = key
        self.emit('stimulus', kind='speech', key=key, channel=channel, clip_seconds=len(samples) / 16000, at=at)
        self.controller.audio.queues[channel].put_nowait((samples, at))

    @objc.python_method
    def view_state(self):
        if not hasattr(self, 'controller'):
            return {}
        browser = self.controller.browser
        return {'active_type': browser.active_key, 'frozen': browser.frozen,
                'visible_id': browser.visible.id if browser.visible else None,
                'content_hash': digest(self.controller.copy_text()),
                'types': {key: {'versions': len(value.versions), 'newer': value.newer_count,
                                'cursor': value.cursor, 'following': value.following}
                          for key, value in browser.state.types.items()}}

    @objc.python_method
    def qualifies(self, snapshot):
        keys = {self.sources.get(o.id) for o in snapshot.observations}
        required = {'initial': {'screen:initial', 'initial_user', 'initial_other'},
                    'unchanged': {'ack'}, 'changed_held': {'screen:changed', 'change'},
                    'participant_question': {'question'}, 'passive_pivot': {'screen:checklist', 'pivot'}}
        if self.stage == 'superseded_task':
            return self.stale_phase == 'new' and snapshot.session_id == self.controller.context.session_id and 'screen:new_task' in keys
        return required[self.stage] <= keys

    @objc.python_method
    def observe(self, event, before):
        kind, c = event['type'], self.controller
        if kind in {'capture_done', 'speech'}:
            key = 'screen:' + self.frames.get(event.get('capture_id'), 'unknown') if kind == 'capture_done' else self.speech_times.get(event['at'])
            matching = [o for o in c.context.observations if o.at == event.get('at')]
            for observation in matching:
                self.sources[observation.id] = key
            if kind == 'capture_done' and 'screen' in event and matching:
                self.screen_completions += 1
            self.emit('perception', type=kind, stimulus=key, observations=[o.model_dump(exclude={'image_path'}) for o in matching],
                      elapsed=event.get('seconds'), error=event.get('error'), accepted=bool(matching))
        if kind == 'context_built':
            self.emit('memory', update=event['update'].model_dump() if 'update' in event else None,
                      error=event.get('error'), notes=event.get('notes'),
                      context=c.context.snapshot().prompt_context())
        if kind not in {'result', 'error', 'cancelled', 'unchanged'}:
            return
        job = event['job']
        accepted = self.accepted.pop(id(event), False)
        after = self.view_state()
        published = accepted and kind == 'result' and not event.get('duplicate')
        view = None
        if published:
            c.window.displayIfNeeded()
            c.window.flushWindow()
            view = f'views/{len(self.rows):04d}-{job.lane}.png'
            c.save_view_image(self.directory / view)
        current_ids = {o.id for o in c.context.snapshot().observations}
        snapshot_ids = {o.id for o in job.snapshot.observations}
        row = self.emit('terminal', type=kind, lane=job.lane, request_id=job.request_id,
                        accepted=accepted, published=published, duplicate=event.get('duplicate', False),
                        qualified=self.qualifies(job.snapshot), snapshot=job.snapshot.prompt_context(),
                        session_id=job.snapshot.session_id, current_session_id=c.context.session_id,
                        task_revision=job.snapshot.task_revision, current_task_revision=c.context.task_revision,
                        request_elapsed=time.monotonic() - job.started,
                        newer_observation_ids=sorted(current_ids - snapshot_ids),
                        reason=event.get('reason'), error=event.get('message'),
                        response=event['response'].model_dump() if 'response' in event else {},
                        plan=event['response']._task_plan if 'response' in event else None,
                        delivery_notes=event.get('delivery_notes'), before=before, after=after, view=view,
                        selected_pane_changed=before.get('content_hash') != after.get('content_hash'))
        self.terminals.append(row)

    @objc.python_method
    def transition(self, stage, outcome='completed'):
        self.emit('stage_finished', outcome=outcome, elapsed=time.monotonic() - self.stage_started,
                  view_state=self.view_state())
        self.stage, self.stage_started = stage, time.monotonic()
        self.terminals, self.screen_completions = [], 0
        self.emit('stage_started')
        c = self.controller
        if stage == 'unchanged':
            key = next((key for key, stream in c.browser.state.types.items()
                        if stream.visible and stream.visible.artifact and stream.visible.artifact.kind == 'code'), None)
            if key:
                c.browser.select_type(key)
                c.show_selected_output()
                self.hold_state = self.view_state()
                self.emit('hold', state=self.hold_state)
            else:
                self.emit('hold', error='No code output available to hold')
            self.speak('ack')
        elif stage == 'changed_held':
            self.screen_key = 'changed'
            self.emit('stimulus', kind='screen', key='changed')
            self.speak('change')
        elif stage == 'participant_question':
            self.emit('hold', initial=self.hold_state, current=self.view_state())
            c.latestOutput_(None)
            c.window.displayIfNeeded()
            self.emit('resume', state=self.view_state())
            self.speak('question')
        elif stage == 'passive_pivot':
            self.screen_key = 'checklist'
            self.emit('stimulus', kind='screen', key='checklist')
            self.speak('pivot')
        elif stage == 'superseded_task':
            self.stale_phase = 'waiting_old'
            c.helpNow_(None)

    def tick_(self, timer):
        if self.finished:
            return
        try:
            elapsed = time.monotonic() - self.stage_started
            if time.monotonic() - self.started > 1200 or self.calls > 120:
                return self.finish('run_limit')
            if elapsed > (180 if self.stage == 'unchanged' else 240):
                self.emit('stage_timeout', elapsed=elapsed)
                # Preserve an incomplete final probe, otherwise continue to observe remaining dimensions.
                if self.stage == STAGES[-1]:
                    return self.finish('stage_timeout')
                self.transition(STAGES[STAGES.index(self.stage) + 1], outcome='timeout')
                return
            if self.stage == 'superseded_task' and self.stale_phase == 'ready_to_switch':
                c = self.controller
                c.newTask_(None)
                self.screen_key = 'new_task'
                self.emit('stimulus', kind='screen', key='new_task')
                self.stale_phase = 'new'
                before = self.view_state()
                c.handle_event(self.held_event)
                self.emit('stale_released', before=before, after=self.view_state(),
                          rejected=not self.terminals[-1]['accepted'], request_id=self.held_event['job'].request_id)
                self.held_event = None
                c.toggleRunning_(None)
                return
            qualified = [e for e in self.terminals if e['qualified'] and e['lane'] == 'deep']
            if not qualified:
                return
            if self.stage == 'unchanged' and (elapsed < 30 or self.screen_completions < 2):
                return
            if self.stage == STAGES[-1]:
                return self.finish('completed')
            self.transition(STAGES[STAGES.index(self.stage) + 1])
        except Exception:
            self.emit('failure', error=traceback.format_exc())
            self.finish('harness_error')

    @objc.python_method
    def finish(self, outcome):
        self.finished = True
        self.emit('stage_finished', outcome=outcome, elapsed=time.monotonic() - self.stage_started, view_state=self.view_state())
        self.timer.invalidate()
        self.controller.shutdown()
        self.controller.window.orderOut_(None)
        self.manifest.update(completed=outcome == 'completed', outcome=outcome, provider_calls=self.calls,
                             elapsed=time.monotonic() - self.started,
                             runtime_source_unchanged=release_manifest()['code_hash'] == self.manifest['release']['code_hash'])
        private_write(self.directory / 'manifest.json', json.dumps(self.manifest, indent=2))
        render(self.directory)
        print(json.dumps({'outcome': outcome, 'review': str(self.directory / 'index.html'), 'calls': self.calls}), flush=True)
        A.NSApp.terminate_(None)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true', help='Generate silent fixtures and freeze configuration without inference')
    args = parser.parse_args()
    current = load_settings()
    choices = {key: getattr(current, key) for key in ('quick', 'deep', 'ocr', 'planner', 'context_builder')}
    if any(choice.provider != 'codex' for choice in choices.values()):
        raise ValueError('This recorded baseline requires the configured Codex lanes; no provider fallback is allowed')
    settings = Settings(configured=True, transcription='local', local_asr_model=current.local_asr_model, **choices)
    directory = args.output.resolve()
    manifest = prepare(directory, settings)
    if args.prepare:
        print(directory)
        return
    os.environ['OTSC_DATA_DIR'] = str(directory / 'private-app-state')
    os.environ['HF_HUB_OFFLINE'] = '1'
    app = A.NSApplication.sharedApplication()
    app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
    run = NativeContinuity.alloc().initWithDirectory_settings_manifest_(str(directory), settings, manifest)
    app.setDelegate_(run.controller)
    print('Native continuity review: ' + str(directory), flush=True)
    AppHelper.runEventLoop()


if __name__ == '__main__':
    main()
