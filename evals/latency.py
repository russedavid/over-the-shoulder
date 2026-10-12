"""Compare the retained baseline with the independent, streamed assistance path.

Run explicitly with --live; synthetic observations only, no capture or audio.
Raw prompts, responses, and traces belong in a private output directory.
"""

import argparse
import json
import queue
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

from evals.planning_comparison import CASES, RecordedProvider, background, content_check, seed
from otsc.codex_server import close_servers
from otsc.context_builder import apply_update
from otsc.planning import CombinedTaskProvider, TaskPlanningProvider
from otsc.privacy import private_directory, private_write, redact
from otsc.providers import provider_for
from otsc.quick import QuickProvider
from otsc.scheduler import Cancellation, Coordinator
from otsc.settings import Credentials, load_settings
from otsc.telemetry import TraceStore, digest, release_manifest


def run_case(case, variant, directory, settings):
    private_directory(directory)
    context, previous = seed(case, warm_memory=True)
    snapshot = replace(context.snapshot(), automatic_refresh=True)
    started = time.monotonic()
    choices = settings.model_copy(deep=True)
    for lane in ("quick", "deep", "planner", "context_builder"):
        getattr(choices, lane).codex_transport = "exec" if variant == "baseline" else "app-server"
    providers = {lane: RecordedProvider(provider_for(getattr(choices, lane), Credentials()), lane, directory, started)
                 for lane in ("quick", "deep", "planner", "context_builder")}
    quick = providers["quick"] if variant == "baseline" else QuickProvider(providers["quick"])
    deep = TaskPlanningProvider(providers["deep"], providers["planner"]) if variant == "baseline" else CombinedTaskProvider(providers["deep"])
    trace = TraceStore(directory / "trace", source="latency-comparison")
    coordinator = Coordinator(context, lambda lane: quick if lane == "quick" else deep,
                              trace=trace, independent_quick=variant != "baseline")
    coordinator.current = previous
    token = Cancellation()
    executor = ThreadPoolExecutor(max_workers=1)
    memory = executor.submit(background, snapshot, providers["context_builder"], token, trace, False)
    row = {"case": case, "variant": variant, "input_hash": digest(snapshot.prompt_context()),
           "expected_answer": CASES[case]["needed"], "first_preview_seconds": None,
           "quick_seconds": None, "deep_seconds": None, "no_change_seconds": {}, "responses": {}, "errors": [],
           "unnecessary_publications": 0}
    try:
        coordinator.request()
        deadline = time.monotonic() + 300
        while coordinator.active_lanes and time.monotonic() < deadline:
            try:
                event = coordinator.events.get(timeout=.02)
            except queue.Empty:
                continue
            elapsed = time.monotonic() - started
            if event["type"] == "progress":
                if event["lane"] == "quick" and event["message"].startswith(("Draft: ", "Reply draft: ")):
                    if row["first_preview_seconds"] is None:
                        row["first_preview_seconds"] = elapsed
                continue
            if event["type"] not in {"result", "unchanged", "error", "cancelled"}:
                continue
            accepted = coordinator.accept(event)
            if not accepted:
                continue
            lane = event["job"].lane
            if event["type"] == "error":
                row["errors"].append(event["message"])
            elif event["type"] == "unchanged":
                row["no_change_seconds"][lane] = elapsed
            elif event["type"] == "result" and not event.get("duplicate"):
                row[lane + "_seconds"] = elapsed
                row["responses"][lane] = event["response"].model_dump()
                if not row["expected_answer"]:
                    row["unnecessary_publications"] += 1
                if lane == "deep":
                    row["content_check"] = content_check(case, event["response"])
        if coordinator.active_lanes:
            row["errors"].append("Response deadline exceeded")
        try:
            update = memory.result(timeout=240)
            _, notes = apply_update(context, snapshot, update)
            row["context_notes"] = notes
        except Exception as error:
            row["errors"].append("Context builder: " + redact(str(error)))
        if not row["expected_answer"]:
            row["content_check"] = content_check(case, None if not row["responses"] else coordinator.current)
    finally:
        token.cancel()
        coordinator.close()
        executor.shutdown(wait=True)
    row["wall_seconds"] = time.monotonic() - started
    row["calls"] = [call for provider in providers.values() for call in provider.calls]
    row["plan_decision"] = deep.last_decision
    row["quick_check"] = {
        "status": "fail" if row["expected_answer"] and "quick" in row["no_change_seconds"] else "pass",
        "reason": "A new question/requirement/deliverable should not be suppressed as unchanged." if row["expected_answer"]
                  else "Quiet evidence should not generate a quick publication.",
    }
    if not row["expected_answer"] and row["quick_seconds"] is not None:
        row["quick_check"]["status"] = "fail"
    private_write(directory / "result.json", json.dumps(row, indent=2))
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cases", nargs="+", choices=CASES, default=list(CASES))
    parser.add_argument("--current-only", action="store_true")
    args = parser.parse_args()
    if not args.live:
        parser.error("--live is required; this makes Codex calls but captures no screen or audio")
    if args.output.exists():
        parser.error("Choose a new directory; earlier results must be preserved")
    private_directory(args.output)
    settings = load_settings()
    report = {"release": release_manifest(), "scope": "synthetic evidence ready to validated acceptance; native painting excluded",
              "settings_changed": False, "results": [], "models": {k: getattr(settings, k).model_dump() for k in
                                                                    ("quick", "deep", "planner", "context_builder")}}
    private_write(args.output / "runner.py", Path(__file__).read_text())
    source_root = Path(__file__).resolve().parents[1]
    for path in (source_root / "otsc").glob("*.py"):
        private_write(args.output / "source" / path.name, path.read_text())
    try:
        for index, case in enumerate(args.cases):
            variants = ["current"] if args.current_only else (["baseline", "current"] if index % 2 == 0 else ["current", "baseline"])
            for variant in variants:
                print(json.dumps({"starting": case, "variant": variant}), flush=True)
                row = run_case(case, variant, args.output / case / variant, settings)
                report["results"].append(row)
                private_write(args.output / "comparison.json", json.dumps(report, indent=2))
                print(json.dumps({k: row.get(k) for k in ("case", "variant", "first_preview_seconds", "quick_seconds", "deep_seconds",
                                                        "no_change_seconds", "unnecessary_publications", "errors", "content_check", "quick_check")}), flush=True)
    finally:
        close_servers()
    report["completed"] = True
    report["runtime_code_unchanged"] = release_manifest()["code_hash"] == report["release"]["code_hash"]
    private_write(args.output / "comparison.json", json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
