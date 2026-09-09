"""Revalidate recorded drafts without regenerating their answers or code."""

import argparse
import concurrent.futures
import json
from pathlib import Path

from evals.recording_data import default_directory, read_json, write_json
from otsc.context import Snapshot
from otsc.delivery import prepare_response
from otsc.models import Observation
from otsc.providers import provider_for
from otsc.scheduler import Cancellation
from otsc.settings import Credentials, ModelChoice
from otsc.telemetry import Progress, TraceStore, digest, release_manifest


def snapshot_for(session, point):
    data = point["input"]
    def observation(record):
        if record.get("reading"):
            record = {**record, "reading": {**record["reading"], "visible_text": record["text"]}}
        return Observation.model_validate(record)

    return Snapshot(
        session_id=session["session_id"],
        revision=data["context_revision"],
        goal=data["goal"],
        observations=tuple(observation(o) for o in data["observations"]),
        workspace=data["observed_workspace"],
        repo_root="",
        previous_task=data["previous_task"],
        previous_summary=data["previous_summary"],
        previous_artifacts=json.dumps(data["previous_artifacts"]),
        open_questions=tuple(data["unresolved_questions"]),
        constraints=tuple(data["user_constraints"]),
        decisions=tuple(data["user_decisions"]),
        restored=data["restored_history_requires_fresh_evidence"],
        evidence_revision=data.get("evidence_revision", 0),
        memory_revision=data.get("memory_revision", 0),
        context_items=json.dumps(data.get("accumulated_context", [])),
        context_updated_through=data.get("context_updated_through_evidence_revision", -1),
        previous_answer=json.dumps(data.get("previous_answer", {})),
        as_of=data.get("recent_window", {}).get("end", 0),
        recent_observation_ids=tuple(data.get("recent_window", {}).get("observation_ids", ())),
        omitted_observation_ids=tuple(data.get("recent_window", {}).get("omitted_ids_due_to_size_limit", ())),
    )


def run(directory, output):
    directory, output = Path(directory), Path(output)
    if output.exists():
        raise ValueError("Use a new directory so the prior validation result is preserved")
    output.mkdir(parents=True)
    release = release_manifest()
    jobs = [
        (session, point)
        for path in sorted((directory / "replays").glob("*.json"))
        for session in [read_json(path)]
        for point in session["checkpoints"]
    ]

    def one(pair):
        session, point = pair
        raw = point["raw_proposals"]["deep"]["structured"]
        record = {
            "checkpoint_id": point["id"],
            "original_delivered": bool(point["deep"]),
            "original_error": point["errors"],
            "raw_hash": digest(raw),
            "regenerated_answer": False,
        }
        choice = ModelChoice.model_validate(session["models"]["deep"])
        trace = TraceStore(output / "traces", source="validation-replay")
        progress = Progress(lambda text: None, trace, request_id=point["id"], lane="deep")
        try:
            response = prepare_response(
                raw,
                snapshot_for(session, point),
                "deep",
                Cancellation(),
                progress,
                provider=provider_for(choice, Credentials()),
            )
            after = {a.id: a for a in response.artifacts}
            record.update(
                delivered=True,
                response=response.model_dump(),
                delivery_notes=response._delivery_notes,
                summary_preserved=response.summary == raw["summary"],
                original_artifacts_retained=all(a["id"] in after for a in raw["artifacts"]),
                code_preserved=all(
                    a["id"] in after and after[a["id"]].content == a["content"]
                    for a in raw["artifacts"]
                    if a["kind"] == "code"
                ),
            )
        except Exception as error:
            record.update(delivered=False, error=str(error))
        write_json(output / (point["id"] + ".json"), record)
        print(
            json.dumps(
                {
                    k: record.get(k)
                    for k in ("checkpoint_id", "delivered", "original_artifacts_retained", "code_preserved")
                }
            ),
            flush=True,
        )
        return record

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(one, jobs))
    report = {
        "release": release,
        "results": results,
        "source_directory": str(directory),
        "method": "Same saved model drafts; optional metadata isolated, annotations repaired only when needed",
        "before_delivered": sum(r["original_delivered"] for r in results),
        "after_delivered": sum(r["delivered"] for r in results),
        "code_preserved": all(r.get("code_preserved") for r in results),
        "artifacts_retained": all(r.get("original_artifacts_retained") for r in results),
        "source_unchanged": release_manifest()["code_hash"] == release["code_hash"],
    }
    write_json(output / "validation-replay.json", report)
    print(json.dumps({k: v for k, v in report.items() if k not in {"release", "results"}}), flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", default=str(default_directory()))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    run(args.directory, args.output)
