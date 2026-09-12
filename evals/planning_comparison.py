"""Opt-in live comparison of sequential planning and background context/plan review.

This is an experiment, not an application setting. No capture or model call runs
on import. The shipping fixtures are synthetic development cases, not holdout data.
"""

import argparse
import json
import queue
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError
from dataclasses import replace
from html import escape
from pathlib import Path
from statistics import mean, median

from evals.checks import Raised, UnsupportedCode, run_function
from otsc.context import ContextStore
from otsc.context_builder import CONTEXT_PROMPT, ContextUpdate, apply_update
from otsc.models import Artifact, Assistance, ContextItem, LineAnnotation, Record
from otsc.output_browser import OutputBrowser
from otsc.output_history import OutputHistory
from otsc.planning import PLANNING_SYSTEM, PlanDecision, TaskPlanningProvider
from otsc.privacy import private_write, redact
from otsc.providers import provider_for
from otsc.scheduler import Cancellation, Coordinator
from otsc.settings import Credentials, load_settings
from otsc.telemetry import Progress, TraceStore, digest, release_manifest


class ContextReview(Record):
    context_update: ContextUpdate
    decision: PlanDecision


COMBINED_SYSTEM = (
    CONTEXT_PROMPT.replace("Return only the JSON object matching the schema, with all four arrays present.", "")
    + "\nPerform the following task-plan and answer-necessity review in this same pass. "
    "Put the four memory arrays inside context_update and the planning decision inside decision. "
    "Both use the exact supplied evidence and previous answer. Memory wording changes do not warrant another answer.\n"
    + PLANNING_SYSTEM.replace("Return only the specified JSON.", "")
    + "\nReturn only the ContextReview object: context_update and decision. Do not produce the task answer."
)

BASE_CODE = (
    "def delivery_fee(weight_kg):\n"
    "    if weight_kg <= 0:\n"
    "        raise ValueError(\"weight_kg must be positive\")\n"
    "    return 4 + 2 * weight_kg\n"
)
ZERO_CODE = BASE_CODE.replace("<= 0", "< 0").replace("must be positive", "must be nonnegative")
GOAL = (
    "Help implement delivery_fee(weight_kg) in Python. Inputs are finite numeric parcel weights in kilograms. "
    "Reject zero or negative weights with ValueError; otherwise charge 4 dollars plus 2 dollars per kilogram. "
    "Provide the function and a concise explanation."
)
ZERO_REQUEST = "Change the contract: zero-weight parcels are valid and cost the 4-dollar base fee. Still reject negative weights. Update the function."
CASES = {
    "same_screen": {"needed": False, "text": "Editor panel delivery.py, same source as the previous screen. The code panel is in focus; text is legible.\n" + BASE_CODE, "kind": "screen"},
    "acknowledgment": {"needed": False, "text": "Thanks, that answers it.", "kind": "speech", "speaker": "other_people"},
    "irrelevant_ui": {"needed": False, "text": "delivery.py remains unchanged. An editor update is available in the status bar.\n" + BASE_CODE, "kind": "screen"},
    "small_constraint": {"needed": True, "text": ZERO_REQUEST, "kind": "speech", "speaker": "primary_user", "check": "zero"},
    "new_question": {"needed": True, "text": "Could you show me a worked example of the fee for a 3-kilogram parcel?", "kind": "speech", "speaker": "other_people", "check": "example"},
    "new_contract": {"needed": True, "text": "Stop writing the function. I now need a JSON rollout configuration keyed by region. US must have dry_run true and max_daily_shipments 100; CA must have dry_run false and max_daily_shipments 25. Give me that JSON configuration as the primary output, not more code.", "kind": "speech", "speaker": "primary_user", "check": "json"},
    "first_answer": {"needed": True, "text": "Please implement the stated delivery_fee contract.", "kind": "speech", "speaker": "primary_user", "check": "base"},
    "repeat_after_answer": {"needed": False, "text": "The editor still shows the original delivery.py; the proposed zero-weight change has not been applied. Same visible code, with the cursor now on the function name.\n" + BASE_CODE, "kind": "screen"},
}


def seed(case_id, *, warm_memory=False):
    # Each paired arm receives identical timestamps, observation IDs, and content.
    context = ContextStore(session_id="planning-comparison-" + case_id, clock=lambda: 1000.0)
    context.set_goal(GOAL)
    context.add("screen", "delivery.py\n" + BASE_CODE, "screen", at=995)
    if case_id == "repeat_after_answer":
        context.add("speech", ZERO_REQUEST, "microphone", "primary_user", at=996)
    for index, observation in enumerate(context.observations):
        observation.id = f"seed-{index}"
    source_ids = [item.id for item in context.observations]
    zero = case_id == "repeat_after_answer"
    code = ZERO_CODE if zero else BASE_CODE
    contract = "Zero is valid and costs 4 dollars; negative weights raise ValueError." if zero else "Zero and negative weights raise ValueError."
    answer = Assistance(
        task="Implement parcel delivery fees", summary=contract + " Positive weights cost 4 + 2 * weight_kg dollars.",
        conversation=[], observed_files=[], open_questions=[],
        artifacts=[
            Artifact(id="implementation", kind="code", title="Delivery fee", content=code, language="python", path="",
                     basis="example", source_ids=source_ids, nodes=[], edges=[],
                     annotations=[LineAnnotation(line=i, explanation=text) for i, text in enumerate([
                         "Define the delivery-fee function.", "Check the lower boundary for an invalid parcel weight.",
                         "Reject the invalid weight.", "Add the base fee and per-kilogram charge.",
                     ], 1)]),
            Artifact(id="explanation", kind="explanation", title="Fee rules", content=contract + " The charge increases by 2 dollars per kilogram.",
                     basis="discussion", source_ids=source_ids, language="", path="", annotations=[], nodes=[], edges=[]),
        ],
    )
    answer._task_plan = dict(
        task="Implement parcel delivery fees", approach="Validate the weight and calculate the base plus per-kilogram fee.",
        instructions=[contract, "Keep the code compact and explain the boundary and arithmetic."], assumptions=["Inputs are finite numeric weights."],
        quality_checks=[contract, "A 1-kilogram parcel costs 6 dollars.", "Each nonblank code line has an annotation."], source_ids=source_ids,
        outputs=[
            dict(key="implementation", label="Delivery fee", purpose="Supply the working function", instructions=contract + " Use the stated fee formula.", presentation="code", data_schema=""),
            dict(key="explanation", label="Fee rules", purpose="Explain the boundary and fee", instructions="Give a concise explanation and address relevant follow-up questions.", presentation="text", data_schema=""),
        ],
    )
    if case_id != "first_answer":
        context.integrate(answer)
    if warm_memory:
        prior = context.snapshot()
        update = ContextUpdate(upsert=[
            ContextItem(id="deliverable", kind="task", text="Provide delivery_fee(weight_kg) in Python with a concise explanation.",
                        basis="reported", source_ids=["seed-0"]),
            ContextItem(id="fee_contract", kind="requirement", text=contract + " Inputs are finite numeric weights; valid parcels cost 4 + 2 * weight_kg dollars.",
                        basis="reported", source_ids=["seed-0", "seed-2"] if zero else ["seed-0"]),
            ContextItem(id="visible_implementation", kind="progress", text="The visible delivery.py still contains the original nonpositive-weight rejection and the 4 + 2 * weight_kg formula.",
                        basis="observed", source_ids=["seed-1"]),
        ], remove=[], observed_files=[], retire_files=[])
        changed, notes = apply_update(context, prior, update)
        if not changed or notes:
            raise ValueError("Warm-memory fixture failed provenance validation")
    case = CASES[case_id]
    channel = "screen" if case["kind"] == "screen" else "microphone" if case.get("speaker") == "primary_user" else "system"
    observation = context.add(case["kind"], case["text"], channel, case.get("speaker", "not_applicable"), at=1000, keep_repeats=True)
    observation.id = "new-evidence"
    return context, None if case_id == "first_answer" else answer


def review_key(snapshot):
    """A result cannot approve new evidence or an answer it never reviewed."""
    return (snapshot.session_id, snapshot.task_revision, snapshot.evidence_revision, snapshot.automatic_refresh,
            digest(snapshot.previous_answer), digest(snapshot.task_plan), digest(snapshot.verified_files))


class PreparedReview:
    """One-use, source-bound handoff from a running background review to generation."""

    def __init__(self, snapshot, future: Future):
        self.key, self.future, self.used = review_key(snapshot), future, False

    def generate_json(self, snapshot, lane, token, progress, **kwargs):
        if self.used or review_key(snapshot) != self.key:
            raise ValueError("Background plan review is consumed or stale; review current state again")
        while True:
            token.check()
            try:
                result = self.future.result(timeout=0.1)
                break
            except TimeoutError:
                if self.future.done():
                    raise
                continue
        token.check()
        self.used = True
        return result.decision.model_dump()


class RecordedProvider:
    def __init__(self, base, role, directory, started):
        self.base, self.role, self.directory, self.started = base, role, directory, started
        self.calls = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def call(self, method, snapshot, lane, token, progress, **kwargs):
        row = {"role": self.role, "lane": lane, "started_seconds": time.monotonic() - self.started,
               "model": self.choice.model_dump(), "prompt_hash": digest(kwargs.get("system", "") + kwargs.get("prompt", "")),
               "schema_hash": digest(kwargs.get("schema", {}))}
        self.calls.append(row)
        path = self.directory / f"{self.role}-{len(self.calls):02d}.json"
        record = {"metadata": row, "snapshot": snapshot.prompt_context(), "request": kwargs}
        private_write(path, json.dumps(record, indent=2))
        try:
            result = getattr(self.base, method)(snapshot, lane, token, progress, **kwargs)
            record["result"] = result.model_dump() if hasattr(result, "model_dump") else result
            row["outcome"] = "success"
            return result
        except Exception as error:
            row["outcome"] = "error"
            record["error"] = redact(str(error))
            raise
        finally:
            row["finished_seconds"] = time.monotonic() - self.started
            row["duration_seconds"] = row["finished_seconds"] - row["started_seconds"]
            private_write(path, json.dumps(record, indent=2))

    def generate_json(self, *args, **kwargs):
        return self.call("generate_json", *args, **kwargs)

    def generate(self, *args, **kwargs):
        return self.call("generate", *args, **kwargs)


def background(snapshot, provider, token, trace, combined):
    prompt = snapshot.prompt_context()
    prompt["verified_local_files"] = json.loads(snapshot.verified_files)
    schema = ContextReview.model_json_schema() if combined else ContextUpdate.model_json_schema()
    raw = provider.generate_json(snapshot, "context_builder", token, Progress(lambda text: None, trace, lane="context_builder"),
                                 schema=schema, system=COMBINED_SYSTEM if combined else CONTEXT_PROMPT,
                                 prompt="Review this immutable task snapshot:\n" + json.dumps(prompt, ensure_ascii=False))
    return ContextReview.model_validate(raw) if combined else ContextUpdate.model_validate(raw)


def content_check(case_id, response):
    if not CASES[case_id]["needed"]:
        return {"status": "pass" if response is None else "fail", "reason": "No substantive new input warrants another deep answer."}
    if response is None:
        return {"status": "fail", "reason": "The necessary answer was not delivered."}
    check = CASES[case_id].get("check")
    if check in {"base", "zero"}:
        candidates = [a.diff_context.after if a.diff_context else a.content for a in response.artifacts if a.kind in {"code", "patch"}]
        errors = []
        for code in candidates:
            try:
                for weight, expected in [(0, 4 if check == "zero" else "ValueError"), (-1, "ValueError"), (1, 6), (3, 10), (0.5, 5)]:
                    try:
                        actual = run_function(code, "delivery_fee", [weight])
                    except Raised as error:
                        actual = error.name
                    if actual != expected:
                        errors.append(f"weight {weight}: expected {expected}, received {actual}")
                return {"status": "fail" if errors else "pass", "reason": "; ".join(errors) or "Five restricted-AST behavior checks passed."}
            except (UnsupportedCode, SyntaxError, ValueError) as error:
                errors.append(str(error))
        return {"status": "needs_review" if candidates else "fail", "reason": "; ".join(errors) or "No code was delivered."}
    if check == "json":
        expected = {"US": {"dry_run": True, "max_daily_shipments": 100}, "CA": {"dry_run": False, "max_daily_shipments": 25}}
        for artifact in response.artifacts:
            if artifact.kind == "structured" and artifact.id != "_assistance_plan":
                value = json.loads(artifact.content)
                if value == expected or isinstance(value, dict) and expected in value.values():
                    return {"status": "pass", "reason": "Task-defined JSON contains the exact region configuration."}
        return {"status": "needs_review", "reason": "Inspect the rollout configuration and plan; exact object not found."}
    return {"status": "needs_review", "reason": "Inspect whether the participant's question is answered with a 10-dollar worked example."}


def run_arm(case_id, arm, directory, settings, *, background_busy=False, warm_memory=False):
    directory.mkdir(parents=True, exist_ok=False)
    context, prior = seed(case_id, warm_memory=warm_memory)
    snapshot = replace(context.snapshot(), automatic_refresh=True)
    private_write(directory / "input.json", json.dumps(snapshot.prompt_context(), indent=2))
    trace = TraceStore(directory / "trace", source="planning-comparison")
    credentials = Credentials()
    started = time.monotonic()
    models = {role: RecordedProvider(provider_for(getattr(settings, role), credentials), role, directory, started)
              for role in ("context_builder", "planner", "deep", "quick")}
    token = Cancellation()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="otsc-eval-context")
    warmup = None
    if background_busy:
        older = replace(snapshot, observations=tuple(o for o in snapshot.observations if o.id != "new-evidence"),
                        recent_observation_ids=tuple(sid for sid in snapshot.recent_observation_ids if sid != "new-evidence"),
                        evidence_revision=snapshot.evidence_revision - 1)
        warmup = executor.submit(background, older, models["context_builder"], token, trace, arm == "combined")
        # The old review is real provider work. Inject the new input one second
        # later, excluding that earlier second from fresh-answer latency.
        time.sleep(1)
        arrival = time.monotonic()
        shift = arrival - started
        started = arrival
        for provider in models.values():
            provider.started = started
            for call in provider.calls:
                for key in ("started_seconds", "finished_seconds"):
                    if key in call:
                        call[key] -= shift
        busy_at_arrival = not warmup.done()
    future = executor.submit(background, snapshot, models["context_builder"], token, trace, arm == "combined")
    planner = PreparedReview(snapshot, future) if arm == "combined" else models["planner"]
    planned = TaskPlanningProvider(models["deep"], planner)
    coordinator = Coordinator(context, lambda lane: planned if lane == "deep" else models["quick"], trace=trace)
    coordinator.current = prior
    history, browser = OutputHistory(), OutputBrowser()
    if prior:
        browser.ingest(history.append(prior, session_id=context.session_id, goal=context.goal, lane="deep", sources=snapshot.observations))
    initial_history = len(history.entries)
    response, applied = None, False
    row = {"case": case_id, "arm": arm, "expected_update": CASES[case_id]["needed"], "input_hash": digest(snapshot.prompt_context()),
           "quick_seconds": None, "deep_seconds": None, "decision_seconds": None, "events": [], "error": None,
           "background_busy_at_arrival": busy_at_arrival if warmup else False, "warm_memory": warm_memory}
    try:
        if not coordinator.request():
            raise RuntimeError("The comparison request was not accepted")
        deadline = time.monotonic() + 600
        while coordinator.active_lanes and time.monotonic() < deadline:
            if future.done() and not applied:
                try:
                    result = future.result()
                    update = result.context_update if isinstance(result, ContextReview) else result
                    changed, notes = apply_update(context, snapshot, update)
                    row["context_update"] = {"changed": changed, "notes": notes}
                except Exception as error:
                    row["context_error"] = redact(str(error))
                applied = True
            try:
                event = coordinator.events.get(timeout=0.02)
            except queue.Empty:
                continue
            if event["type"] not in {"result", "unchanged", "error", "cancelled"}:
                continue
            accepted = coordinator.accept(event)
            now = time.monotonic() - started
            row["events"].append({"type": event["type"], "lane": event["job"].lane, "seconds": now,
                                  "accepted": accepted, "duplicate": event.get("duplicate", False)})
            if event["type"] == "error":
                row["error"] = event["message"]
            if not accepted:
                continue
            if event["type"] == "result" and not event.get("duplicate"):
                lane = event["job"].lane
                row[lane + "_seconds"] = now
                current = event["response"]
                browser.ingest(history.append(current, session_id=context.session_id, goal=context.goal, lane=lane,
                                              sources=event["job"].snapshot.observations))
                if lane == "deep":
                    response = current
                    private_write(directory / "answer.json", current.model_dump_json(indent=2))
            elif event["type"] == "unchanged":
                row["decision_seconds"] = now
        if coordinator.active_lanes:
            raise TimeoutError("Comparison request exceeded its deadline")
    except Exception as error:
        row["error"] = redact(str(error))
    finally:
        coordinator.close()
        try:
            result = future.result(timeout=260)
            if not applied:
                update = result.context_update if isinstance(result, ContextReview) else result
                changed, notes = apply_update(context, snapshot, update)
                row["context_update"] = {"changed": changed, "notes": notes}
        except Exception as error:
            row["context_error"] = redact(str(error))
        if warmup:
            try:
                warmup.result()
            except Exception as error:
                row["warmup_error"] = redact(str(error))
        token.cancel()
        executor.shutdown(wait=True)
    row["decision"] = planned.last_decision
    row["plan"] = planned.last_plan
    row["response"] = response.model_dump() if response else None
    row["content_check"] = content_check(case_id, response)
    row["calls"] = [call for provider in models.values() for call in provider.calls]
    review_calls = models["context_builder" if arm == "combined" else "planner"].calls
    if row["decision_seconds"] is None and review_calls:
        row["decision_seconds"] = review_calls[-1].get("finished_seconds")
    context_calls = models["context_builder"].calls
    row["context_queue_wait_seconds"] = context_calls[-1]["started_seconds"] if context_calls else None
    row["deep_generation_calls"] = len([c for c in models["deep"].calls if c["lane"] == "deep"])
    row["history_entries_added"] = len(history.entries) - initial_history
    row["unnecessary_generations"] = (row["deep_generation_calls"] + len(models["quick"].calls)) if not row["expected_update"] else 0
    row["unnecessary_publications"] = row["history_entries_added"] if not row["expected_update"] else 0
    row["decision_correct"] = bool(row["decision"]) and row["decision"]["answer_needed"] == row["expected_update"]
    row["wall_seconds"] = time.monotonic() - started
    private_write(directory / "result.json", json.dumps(row, indent=2))
    return row


def summarize(report):
    summary = {"arms": {}, "paired_fresh_answers": []}
    for arm in ("current", "combined"):
        rows = [row for row in report["results"] if row["arm"] == arm]
        fresh = [row for row in rows if row["expected_update"] and row["deep_seconds"] is not None and not row["error"]]
        quiet = [row for row in rows if not row["expected_update"]]
        timings = [row["deep_seconds"] for row in fresh]
        quick = [row["quick_seconds"] for row in fresh if row["quick_seconds"] is not None]
        quiet_times = [row["decision_seconds"] for row in quiet if row["decision_seconds"] is not None]
        summary["arms"][arm] = {
            "trials": len(rows), "decisions_correct": sum(row["decision_correct"] for row in rows),
            "required_answers": sum(row["expected_update"] for row in rows), "fresh_answers_delivered": len(fresh),
            "median_deep_seconds": median(timings) if timings else None,
            "mean_deep_seconds": mean(timings) if timings else None,
            "median_quick_seconds": median(quick) if quick else None,
            "median_no_update_seconds": median(quiet_times) if quiet_times else None,
            "no_update_trials": len(quiet), "unnecessary_generations": sum(row["unnecessary_generations"] for row in quiet),
            "unnecessary_publications": sum(row["unnecessary_publications"] for row in quiet),
            "model_calls": sum(len(row["calls"]) for row in rows),
            "errors": sum(bool(row.get("error") or row.get("context_error") or row.get("warmup_error")) for row in rows),
        }
    by_case = {}
    for row in report["results"]:
        if row["expected_update"] and row["deep_seconds"] is not None and not row["error"]:
            by_case.setdefault((row["round"], row["case"]), {})[row["arm"]] = row
    for (trial, case), pair in by_case.items():
        if set(pair) == {"current", "combined"}:
            current, combined = pair["current"]["deep_seconds"], pair["combined"]["deep_seconds"]
            summary["paired_fresh_answers"].append({"round": trial, "case": case, "current": current, "combined": combined,
                                                     "combined_minus_current_seconds": combined - current})
    differences = [row["combined_minus_current_seconds"] for row in summary["paired_fresh_answers"]]
    summary["median_paired_difference_seconds"] = median(differences) if differences else None
    summary["combined_faster_pairs"] = sum(value < 0 for value in differences)
    return summary


def render(directory, report):
    def number(value):
        return "—" if value is None else f"{value:.2f}s"

    rows, details = [], []
    for index, result in enumerate(report["results"]):
        decision = result.get("decision") or {}
        status = result["content_check"]["status"]
        if status == "needs_review" and result.get("assistant_review", {}).get("status") == "pass":
            status = "pass (assistant)"
        rows.append(f"<tr><td><a href='#r{index}'>{escape(result['case'])}</a></td><td>{result['round']}</td>"
                    f"<td>{result['arm']}</td><td>{result['expected_update']}</td><td>{decision.get('answer_needed', 'error')}</td>"
                    f"<td>{number(result['decision_seconds'])}</td><td>{number(result['quick_seconds'])}</td><td>{number(result['deep_seconds'])}</td>"
                    f"<td>{result['unnecessary_publications']}</td><td>{escape(status)}</td></tr>")
        detail = {key: result.get(key) for key in ("decision", "content_check", "assistant_review", "error", "context_update", "context_error", "warmup_error", "calls", "events")}
        output = []
        for reply in (result.get("response") or {}).get("conversation", []):
            output.append("<p><b>Suggested reply:</b> " + escape(reply["text"]) + "</p>")
        for artifact in (result.get("response") or {}).get("artifacts", []):
            if artifact["id"] != "_assistance_plan":
                output.append("<h3>" + escape(artifact["title"]) + "</h3><pre>" + escape(artifact["content"]) + "</pre>")
        links, counts = [], {}
        for call in result["calls"]:
            role = call["role"]
            counts[role] = counts.get(role, 0) + 1
            links.append(f"<a href='{result['path']}/{role}-{counts[role]:02d}.json'>{escape(role)} {counts[role]}: {number(call.get('duration_seconds'))}</a>")
        details.append(f"<details id='r{index}'><summary>{escape(result['case'])} · round {result['round']} · {result['arm']}</summary>"
                       f"<p><a href='{result['path']}/input.json'>Input</a> · <a href='{result['path']}/result.json'>Full result</a></p>"
                       + "<p>Calls: " + " · ".join(links) + "</p>" + "".join(output)
                       + f"<details><summary>Decision, checks and timing trace</summary><pre>{escape(json.dumps(detail, indent=2))}</pre></details></details>")
    page = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>
<title>Background planning comparison</title><style>body{font:15px/1.5 system-ui;background:#f6f0e6;color:#352b22;margin:24px}main{max-width:1500px;margin:auto}table{border-collapse:collapse;width:100%;background:#fffcf5}td,th{padding:9px;border-bottom:1px solid #d6c8b7;text-align:left}details{background:#fffcf5;margin:18px 0;padding:16px}summary{cursor:pointer;font-weight:600}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 ui-monospace,monospace}a{color:#744b2e}</style><main>
<h1>Background planning comparison</h1><p>Live Codex calls on frozen synthetic OCR/transcript evidence. The clock starts when evidence is available; it includes waiting for review and validated answer delivery. OCR/ASR inference, image generation and native painting are excluded. Current runs context-building alongside medium planning then medium generation. Combined replaces the separate planning call with low context-building plus review. Quick assistance, delivery validation, and publication guards are shared.</p>
<p>Paired arms run separately in alternating order. Fixtures and acceptance criteria were fixed before model calls. Assistant-authored development judgments; no human calibration or held-out accuracy claim. Full prompts and results are retained privately.</p>
"""
    if report.get("related_reports"):
        page += "<nav>Comparisons: " + " · ".join(
            "<a href='" + escape(item["href"], quote=True) + "'>" + escape(item["label"]) + "</a>"
            for item in report["related_reports"]
        ) + "</nav>"
    if report.get("background_busy_probe"):
        page += "<p><b>Busy-worker probe:</b> a real review of the older evidence starts one second before the new input. The single background worker then processes the new evidence. That initial second is excluded from the answer timer; any remaining wait is included.</p>"
    if report.get("warm_memory"):
        page += "<p><b>Warm-memory probe:</b> source-backed working notes about the existing task, fee contract and visible code are populated before the new input. Their evidence watermark precedes the new input. Both variants receive the same notes.</p>"
    if report.get("summary"):
        page += "<h2>Measured results</h2><table><tr><th>Metric</th><th>Current</th><th>Combined</th></tr>"
        for label, key in [("Median validated deep answer", "median_deep_seconds"),
                           ("Median quick guidance", "median_quick_seconds"),
                           ("Median no-update decision", "median_no_update_seconds"),
                           ("Needed answers delivered", "fresh_answers_delivered"),
                           ("Unnecessary publications", "unnecessary_publications"),
                           ("Model calls", "model_calls")]:
            values = [report["summary"]["arms"][arm][key] for arm in ("current", "combined")]
            cells = [number(value) if key.endswith("seconds") else str(value) for value in values]
            page += "<tr><td>" + label + "</td>" + "".join("<td>" + value + "</td>" for value in cells) + "</tr>"
        page += "</table>"
        page += "<details><summary>All aggregate and paired timing data</summary><pre>" + escape(json.dumps(report["summary"], indent=2)) + "</pre></details>"
    page += "<table><thead><tr><th>Case</th><th>Round</th><th>Arm</th><th>Update expected</th><th>Update decided</th><th>Review ready</th><th>Quick</th><th>Deep</th><th>Unnecessary history</th><th>Content</th></tr></thead><tbody>"
    page += "".join(rows) + "</tbody></table>" + "".join(details)
    page += """<script>function showResult(){const node=document.getElementById(location.hash.slice(1));if(node instanceof HTMLDetailsElement)node.open=true;}addEventListener('hashchange',showResult);showResult();</script></main></html>"""
    private_write(directory / "index.html", page)


def run(directory, rounds=2, cases=None, *, background_busy=False, warm_memory=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    settings = load_settings()
    selected = list(cases or CASES)
    report = {"release": release_manifest(), "cases_hash": digest(CASES), "combined_prompt_hash": digest(COMBINED_SYSTEM),
              "combined_schema_hash": digest(ContextReview.model_json_schema()), "rounds": rounds, "cases": selected,
              "background_busy_probe": background_busy, "warm_memory": warm_memory, "experiment_hash": digest(Path(__file__).read_text()),
              "models": {role: getattr(settings, role).model_dump() for role in ("context_builder", "planner", "deep", "quick")},
              "labels": "assistant-authored synthetic development expectations", "live_capture": False, "results": []}
    private_write(directory / "runner-source.py", Path(__file__).read_text())
    private_write(directory / "cases.json", json.dumps(CASES, indent=2))
    private_write(directory / "comparison.json", json.dumps(report, indent=2))
    for trial in range(1, rounds + 1):
        for index, case in enumerate(selected):
            arms = ("current", "combined") if (index + trial) % 2 else ("combined", "current")
            for arm in arms:
                path = f"round-{trial}/{case}/{arm}"
                print(json.dumps({"event": "started", "round": trial, "case": case, "arm": arm}), flush=True)
                result = run_arm(case, arm, directory / path, settings, background_busy=background_busy, warm_memory=warm_memory)
                result.update(round=trial, path=path)
                report["results"].append(result)
                private_write(directory / "comparison.json", json.dumps(report, indent=2))
                render(directory, report)
                print(json.dumps({key: result.get(key) for key in ("case", "arm", "decision_correct", "decision_seconds", "deep_seconds", "unnecessary_publications", "content_check", "error")}), flush=True)
            pair = report["results"][-2:]
            if pair[0]["input_hash"] != pair[1]["input_hash"]:
                raise AssertionError("Paired inputs differ")
    report["summary"] = summarize(report)
    private_write(directory / "comparison.json", json.dumps(report, indent=2))
    render(directory, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly authorize live calls through saved provider settings")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    parser.add_argument("--cases", nargs="+", choices=list(CASES))
    parser.add_argument("--background-busy", action="store_true", help="Start a real review of the older evidence one second before the new evidence arrives")
    parser.add_argument("--warm-memory", action="store_true", help="Populate source-backed accumulated notes before the new input")
    args = parser.parse_args()
    if not args.live:
        parser.error("Live inference requires --live")
    if not 1 <= args.rounds <= 5:
        parser.error("Use between one and five rounds")
    run(args.output, args.rounds, args.cases, background_busy=args.background_busy, warm_memory=args.warm_memory)


if __name__ == "__main__":
    main()
