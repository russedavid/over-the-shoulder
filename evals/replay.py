"""Reprocess recorded development outputs without hiding the original judgment."""

import copy
import json
import time
from pathlib import Path
from uuid import uuid4

from evals.cases import CASES, corpus_manifest, make_snapshot
from evals.checks import task_checks
from evals.judge import review
from evals.report import write_report
from evals.runner import outcome, validate_reference
from otsc.models import Assistance, normalize_optional_file_metadata, parse_response
from otsc.providers import provider_for
from otsc.settings import Credentials, ModelChoice
from otsc.telemetry import release_manifest
from otsc.workspace import derive_patches


def replay_run(source, output, reference_check):
    if Path(source).resolve() == Path(output).resolve() or (Path(output) / "run.json").exists():
        raise ValueError("Replay requires a new output directory; original judgments must be preserved")
    original = json.loads((Path(source) / "run.json").read_text())
    cases = {c["id"]: c for c in CASES}
    if any(r["split"] != "development" for r in original["results"]):
        raise ValueError("Rubric/metadata repair is restricted to development runs; do not tune on holdout results")
    choice = ModelChoice.model_validate(reference_check["model"])
    validate_reference(choice, reference_check)
    run = {
        "run_id": "replay-" + uuid4().hex[:10],
        "mode": "recorded-output replay",
        "corpus": corpus_manifest(),
        "manifest": {
            "generation_manifest": original["manifest"],
            "processing_release": release_manifest(),
            "parent_run": original["run_id"],
            "new_generations": False,
            "judge_reference_check": reference_check,
        },
        "results": [],
        "summary": {},
    }
    for old in original["results"]:
        started = time.monotonic()
        item = cases[old["case_id"]]
        snapshot = make_snapshot(item)
        result = copy.deepcopy(old)
        result["original_task_passed"] = old["task_passed"]
        result["original_error"] = old.get("error")
        result["original_review"] = old.get("review")
        result["error"] = None
        result["criteria"] = item["criteria"]
        phase = "processing"
        try:
            if old.get("raw_response"):
                raw = old["raw_response"]
                response = parse_response(json.dumps(raw) if isinstance(raw, dict) else raw)
                normalize_optional_file_metadata(response, snapshot.observations, item["files"])
                response.validate_sources(snapshot.observations, item["files"])
                response = derive_patches(response, item["files"])
            else:
                response = Assistance.model_validate(old["response"])
            result["response"] = response.model_dump()
            result["checks"] = task_checks(item, response, snapshot)
            same = old.get("response") == result["response"] and old["criteria"] == item["criteria"]
            if same and old.get("review"):
                result["review"] = {**old["review"], "reused_for_identical_response_and_criterion": True}
            elif not any(c["passed"] is False for c in result["checks"]):
                phase = "semantic_review"
                result["review"] = {
                    **review(provider_for(choice, Credentials()), item, snapshot, response),
                    "method": "llm_provisional",
                    "human_calibrated": False,
                    "model": choice.model,
                }
            result["task_passed"] = outcome(
                result["checks"], result.get("review") if reference_check["eligible"] else None
            )
        except Exception as error:
            result["task_passed"] = False if phase == "processing" else outcome(result["checks"], None)
            result["review"] = None
            result["error"] = {"phase": phase, "type": type(error).__name__, "message": str(error)}
        result["replay_seconds"] = round(time.monotonic() - started, 3)
        run["results"].append(result)
        print(
            json.dumps({"case": result["case_id"], "before": old["task_passed"], "after": result["task_passed"]}),
            flush=True,
        )
    run["summary"] = {
        "cases": len(run["results"]),
        "passed": sum(r["task_passed"] is True for r in run["results"]),
        "failed": sum(r["task_passed"] is False for r in run["results"]),
        "needs_review": sum(r["task_passed"] is None for r in run["results"]),
        "interpretation": "Same recorded development generations; explicit metadata/rubric repairs. Original results preserved.",
    }
    return run, write_report(run, Path(output))
