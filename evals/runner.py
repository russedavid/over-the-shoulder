"""Run explicit live cases or validate the offline corpus and safety contracts."""

import concurrent.futures
import json
import time
from pathlib import Path
from uuid import uuid4

from evals.anchors import anchors
from evals.cases import CASES, corpus_manifest, make_snapshot
from evals.checks import task_checks
from evals.judge import SYSTEM as JUDGE_SYSTEM
from evals.judge import review
from evals.report import write_report
from otsc.inspection import InspectionProvider
from otsc.privacy import app_directory, atomic_private_write
from otsc.providers import provider_for
from otsc.scheduler import Cancellation
from otsc.settings import Credentials, ModelChoice
from otsc.telemetry import Progress, TraceStore, digest, release_manifest


def validate_corpus():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids)), "Duplicate case IDs"
    families = {split: {c["family"] for c in CASES if c["split"] == split} for split in ("development", "holdout")}
    assert not families["development"] & families["holdout"], "Scenario families cross the split boundary"
    for item in CASES:
        assert item["criteria"] and item["origin"]
        snapshot = make_snapshot(item)
        assert len({o.id for o in snapshot.observations}) == len(snapshot.observations)
        # Scoring rules are never passed to the generator as hidden reference answers.
        assert item["criteria"] not in json.dumps(snapshot.prompt_context())
    return corpus_manifest()


def outcome(checks, verdict):
    if any(c["passed"] is False for c in checks):
        return False
    if verdict and verdict["passed"] is False:
        return False
    if any(c["passed"] is None for c in checks):
        return None
    if verdict and verdict["passed"] is not None:
        return verdict["passed"]
    return None


def run_case(item, choice, judge_choice, inspection, directory, reference_eligible=True):
    started = time.monotonic()
    snapshot = make_snapshot(item)
    trace = TraceStore(directory / "traces", source="evaluation")
    progress = Progress(lambda text: None, trace, request_id=item["id"], session_id=snapshot.session_id, lane="deep")
    base = provider_for(choice, Credentials())
    provider = InspectionProvider(base) if inspection else base
    record = {
        "case_id": item["id"],
        "family": item["family"],
        "split": item["split"],
        "goal": item["goal"],
        "criteria": item["criteria"],
        "observations": [o.model_dump(exclude={"image_path"}) for o in snapshot.observations],
        "files": item["files"],
        "checks": [],
        "review": None,
        "task_passed": None,
        "error": None,
    }
    phase = "generation"
    try:
        response = provider.generate(snapshot, "deep", Cancellation(), progress)
        record["generation_seconds"] = round(time.monotonic() - started, 3)
        record["response"] = response.model_dump()
        phase = "deterministic_checks"
        record["checks"] = task_checks(item, response, snapshot)
        if judge_choice and not any(c["passed"] is False for c in record["checks"]):
            phase = "semantic_review"
            judge = provider_for(judge_choice, Credentials())
            judged_at = time.monotonic()
            judge_progress = Progress(
                lambda text: None, trace, request_id=item["id"], session_id=snapshot.session_id, lane="judge"
            )
            record["review"] = {
                **review(
                    judge,
                    item,
                    snapshot,
                    response,
                    progress=judge_progress,
                    inspection_trace=getattr(provider, "last_trace", []),
                ),
                "method": "llm_provisional",
                "model": judge_choice.model,
                "human_calibrated": False,
                "reference_check_eligible": reference_eligible,
            }
            record["review_seconds"] = round(time.monotonic() - judged_at, 3)
        record["task_passed"] = outcome(record["checks"], record["review"] if reference_eligible else None)
    except Exception as error:
        record["task_passed"] = False if phase == "generation" else outcome(record["checks"], None)
        record["error"] = {"phase": phase, "type": type(error).__name__, "message": str(error)[:2500]}
        if phase == "generation":
            record["raw_response"] = getattr(base, "last_raw_response", None) or getattr(base, "last_raw_text", "")
    record["inspection_trace"] = getattr(provider, "last_trace", [])
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    atomic_private_write(directory / (item["id"] + ".json"), json.dumps(record, indent=2, ensure_ascii=False))
    return record


def calibrate(choice, directory):
    examples = anchors()

    def one(anchor):
        verdict = review(
            provider_for(choice, Credentials()), anchor["case"], make_snapshot(anchor["case"]), anchor["response"]
        )
        return {
            "id": anchor["id"],
            "expected": anchor["expected"],
            "verdict": verdict,
            "label_source": "assistant-authored synthetic reference",
        }

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(one, examples))
    false_positives = sum(r["expected"] is False and r["verdict"]["passed"] is True for r in rows)
    true_positives = sum(r["expected"] is True and r["verdict"]["passed"] is True for r in rows)
    agreement = sum(r["expected"] == r["verdict"]["passed"] for r in rows)
    result = {
        "examples": rows,
        "agreement_count": agreement,
        "count": len(rows),
        "false_positives": false_positives,
        "true_positives": true_positives,
        "eligible": false_positives == 0
        and true_positives >= sum(a["expected"] for a in examples) - 1
        and agreement >= len(examples) - 1,
        "human_calibrated": False,
        "interpretation": "Reference sanity check only; not validation against human judgments.",
    }
    result["model"] = choice.model_dump()
    result["rubric_hash"] = digest(JUDGE_SYSTEM)
    result["anchor_hash"] = digest(
        [
            {
                "id": a["id"],
                "expected": a["expected"],
                "criteria": a["case"]["criteria"],
                "response": a["response"].model_dump(),
            }
            for a in examples
        ]
    )
    atomic_private_write(Path(directory) / "judge-reference-check.json", json.dumps(result, indent=2))
    return result


def validate_reference(choice, reference_check):
    if ModelChoice.model_validate(reference_check.get("model", {})) != choice or reference_check.get("rubric_hash") != digest(
        JUDGE_SYSTEM
    ):
        raise ValueError("The reference check does not match this judge configuration")
    expected = digest(
        [
            {
                "id": a["id"],
                "expected": a["expected"],
                "criteria": a["case"]["criteria"],
                "response": a["response"].model_dump(),
            }
            for a in anchors()
        ]
    )
    if reference_check.get("anchor_hash") != expected:
        raise ValueError("The reference check does not match the current anchor set")


def run_live(
    *,
    split="development",
    limit=None,
    inspection=False,
    judge=True,
    model="gpt-6-astra",
    reasoning="medium",
    fast_mode=True,
    judge_model="gpt-6-astra",
    output=None,
    reference_check=None,
    case_ids=None,
):
    validate_corpus()
    selected = [c for c in CASES if split == "all" or c["split"] == split]
    if case_ids is not None:
        if not set(case_ids) <= {c["id"] for c in selected}:
            raise ValueError("Requested cases do not belong to this split")
        selected = [c for c in selected if c["id"] in case_ids]
    if limit is not None:
        selected = selected[:limit]
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:6]
    directory = Path(output) if output else app_directory() / "evaluations" / run_id
    choice = ModelChoice(provider="codex", model=model, reasoning=reasoning, fast_mode=fast_mode)
    judge_choice = ModelChoice(provider="codex", model=judge_model, reasoning="low") if judge else None
    manifest = {
        "release": release_manifest(),
        "generator": choice.model_dump(),
        "judge": judge_choice.model_dump() if judge_choice else None,
        "corpus": corpus_manifest(),
        "rubric_hash": digest(JUDGE_SYSTEM),
        "inspection": inspection,
        "human_calibrated": False,
        "case_ids": [c["id"] for c in selected],
    }
    manifest["evaluator_hash"] = digest({p.name: p.read_text() for p in sorted(Path(__file__).parent.glob("*.py"))})
    run = {
        "run_id": run_id,
        "mode": "bounded inspection" if inspection else "standard workflow",
        "corpus": corpus_manifest(),
        "manifest": manifest,
        "results": [],
        "summary": {},
    }
    directory.mkdir(parents=True, exist_ok=True)
    if (directory / "manifest.json").exists() or (directory / "run.json").exists():
        raise ValueError("This output directory already contains a run; use a new directory to preserve it")
    if judge_choice:
        if reference_check:
            validate_reference(judge_choice, reference_check)
        reference_check = reference_check or calibrate(judge_choice, directory)
        manifest["judge_reference_check"] = {k: v for k, v in reference_check.items() if k != "examples"}
    eligible = reference_check.get("eligible", False) if judge_choice else False
    atomic_private_write(directory / "manifest.json", json.dumps(manifest, indent=2))
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = {
            pool.submit(run_case, item, choice, judge_choice, inspection, directory, eligible): item
            for item in selected
        }
        for future in concurrent.futures.as_completed(futures):
            result = future.result()
            run["results"].append(result)
            print(
                json.dumps(
                    {"case": result["case_id"], "passed": result["task_passed"], "seconds": result["elapsed_seconds"]}
                ),
                flush=True,
            )
    run["results"].sort(key=lambda r: r["case_id"])
    run["summary"] = {
        "cases": len(selected),
        "passed": sum(r["task_passed"] is True for r in run["results"]),
        "failed": sum(r["task_passed"] is False for r in run["results"]),
        "needs_review": sum(r["task_passed"] is None for r in run["results"]),
        "interpretation": "Task-specific synthetic cases with provisional model judgments; not a field success rate.",
    }
    run["manifest"]["source_unchanged_during_run"] = release_manifest()["code_hash"] == manifest["release"]["code_hash"]
    report = write_report(run, directory)
    print(json.dumps({"report": str(report), **run["summary"]}), flush=True)
    return run, report
