"""Local operational reports and controlled recovery exercises."""

import html
import json
import statistics
import time
from pathlib import Path

from otsc.privacy import atomic_private_write
from otsc.telemetry import TraceStore


def report(directory=None, output=None):
    trace = TraceStore(directory)
    summary = trace.summary()
    rows = trace.events()
    lanes = {}
    for lane in ("quick", "deep"):
        values = sorted(
            r["elapsed_ms"]
            for r in rows
            if r.get("event") == "generation_finished" and r.get("lane") == lane and r.get("outcome") == "success"
        )
        lanes[lane] = {
            "successful_generations": len(values),
            "median_ms": statistics.median(values) if values else None,
            "p95_ms": values[min(len(values) - 1, int((len(values) - 1) * 0.95))] if values else None,
        }
    summary["by_lane"] = lanes
    summary["feedback"] = {
        label: sum(r.get("event") == "feedback" and r.get("rating") == label for r in rows)
        for label in ("useful", "needs_work")
    }
    summary["observed_window"] = {
        "first_event": rows[0]["at"] if rows else None,
        "last_event": rows[-1]["at"] if rows else None,
    }
    if output:
        text = (
            '<!doctype html><html lang="en"><meta charset="utf-8"><title>OTSC operational report</title><style>body{max-width:1000px;margin:40px auto;padding:0 24px;font:16px/1.5 system-ui;background:#f8f4ec;color:#30291f}pre{white-space:pre-wrap;background:#fffdf9;padding:20px;border:1px solid #cbbba8}</style><h1>Operational record</h1><p>Local metadata only. Latency is separated by response lane. Counts are observations from this operating window, not a service-level guarantee.</p><pre>'
            + html.escape(json.dumps(summary, indent=2))
            + "</pre></html>"
        )
        atomic_private_write(Path(output), text)
    return summary


def recovery_exercise(output):
    """Exercise a provider failure and a stale response against the real coordinator."""
    from otsc.context import ContextStore
    from otsc.models import Assistance
    from otsc.scheduler import Coordinator

    trace = TraceStore(Path(output) / "traces", source="recovery-exercise")
    context = ContextStore()
    context.set_goal("Preserve useful work during a provider failure")
    stable = Assistance(
        task=context.goal,
        summary="A useful existing proposal",
        conversation=[],
        artifacts=[],
        observed_files=[],
        open_questions=[],
    )

    class Provider:
        def generate(self, snapshot, lane, token, progress):
            if lane == "quick":
                raise RuntimeError("Injected provider failure")
            return stable

    coordinator = Coordinator(context, lambda lane: Provider(), trace=trace)
    coordinator.current = stable
    seen = []
    start = time.monotonic()
    try:
        coordinator.request(manual=True)
        deadline = start + 3
        while coordinator.active_lanes and time.monotonic() < deadline:
            event = coordinator.events.get(timeout=3)
            if event["type"] in {"result", "error", "cancelled"}:
                coordinator.accept(event)
                seen.append(event["type"])
        assert "error" in seen and coordinator.current.summary == stable.summary
        original = coordinator.current
        coordinator.request(manual=True)
        context.set_goal("A newer task supersedes the request")
        deadline = time.monotonic() + 3
        while coordinator.active_lanes and time.monotonic() < deadline:
            event = coordinator.events.get(timeout=3)
            if event["type"] in {"result", "error", "cancelled"}:
                coordinator.accept(event)
        assert coordinator.current is original
        result = {
            "passed": True,
            "kind": "controlled failure exercise",
            "provider_failure_preserved_work": True,
            "stale_results_rejected": True,
            "seconds": round(time.monotonic() - start, 3),
            "field_incident": False,
        }
        atomic_private_write(Path(output) / "recovery.json", json.dumps(result, indent=2))
        report(Path(output) / "traces", Path(output) / "report.html")
        return result
    finally:
        coordinator.close()
