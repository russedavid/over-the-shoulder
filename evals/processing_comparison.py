"""Compare processing or reasoning settings on one frozen recorded checkpoint."""

import argparse
import base64
import json
import time
from dataclasses import replace
from html import escape
from pathlib import Path

from evals.recording_data import read_json, write_json
from evals.validation_replay import snapshot_for
from otsc.codex import CodexProvider
from otsc.models import Assistance
from otsc.privacy import private_write
from otsc.prompts import SYSTEM, build_prompt
from otsc.providers import latest_image
from otsc.scheduler import Cancellation
from otsc.settings import ModelChoice
from otsc.telemetry import Progress, TraceStore, digest, release_manifest

VARIANTS = {
    "processing": (("standard", "medium", False), ("fast", "medium", True)),
    "reasoning-fast": (("medium-fast", "medium", True), ("low-fast", "low", True)),
}


def render_report(directory, report):
    columns = []
    for row in report["results"]:
        sections = []
        response = row.get("response")
        if response:
            parsed = Assistance.model_validate(response)
            sections.append("<p>" + escape(parsed.summary) + "</p>")
            for reply in parsed.conversation:
                sections.append(
                    f"<p><b>{escape(reply.action.capitalize())}:</b> {escape(reply.text)}"
                    f"<br><small>{escape(reply.source_id)}</small></p>"
                )
            for artifact in parsed.artifacts:
                sections.append(
                    f"<h3>{escape(artifact.title)}</h3><small>{escape(artifact.kind)} · "
                    f"{escape(artifact.basis)}</small><pre>{escape(artifact.content)}</pre>"
                )
                if artifact.annotations:
                    sections.append(
                        "<details><summary>Line explanations</summary><pre>"
                        + escape(artifact.annotated_text())
                        + "</pre></details>"
                    )
            if parsed.open_questions:
                sections.append("<h3>Open questions</h3><ul>")
                sections += ["<li>" + escape(q) + "</li>" for q in parsed.open_questions]
                sections.append("</ul>")
        if row.get("error"):
            sections.append("<pre>" + escape(row["error"]) + "</pre>")
        label = row["model"]["reasoning"].capitalize() + (" · Fast" if row["fast_mode"] else " · Standard")
        sections.insert(
            0,
            f"<h2>{label}</h2><p class='time'>{row['elapsed_seconds']:.2f} seconds</p>"
            f"<p>{row.get('usage', {}).get('output_tokens', 'Unreported')} output tokens · "
            f"{row.get('usage', {}).get('cached_tokens', 'Unreported')} cached input tokens</p>"
            f"<p><a href='{row['mode']}.json'>Raw response, adjustments and usage</a></p>",
        )
        columns.append("<article>" + "".join(sections) + "</article>")
    review = report.get("assistant_review", {})
    critique = ""
    if review:
        critique = "<section><details><summary>Assistant review · provisional</summary><p>" + escape(review["summary"]) + "</p>"
        for label, notes in review.get("variants", {}).items():
            critique += f"<h3>{escape(label)}</h3><ul>"
            critique += "".join("<li>" + escape(n) + "</li>" for n in notes)
            critique += "</ul>"
        critique += "</details></section>"
    title = "Astra Fast: medium vs low" if report.get("comparison") == "reasoning-fast" else "Astra medium: Standard vs Fast"
    first = "Medium/Fast" if report.get("comparison") == "reasoning-fast" else "Standard"
    page = """<!doctype html><html lang='en'><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<title>__TITLE__</title><style>
body{margin:0;background:#f7f3eb;color:#332d26;font:16px/1.55 system-ui,sans-serif}
main{max-width:1500px;margin:auto;padding:28px}h1{margin-bottom:8px}h2{font-size:21px}
a{color:#755131}.grid{display:grid;grid-template-columns:1fr 1fr;gap:22px}
article,section{background:#fffdf8;border:1px solid #dcd2c3;border-radius:12px;padding:22px;margin:20px 0;min-width:0}
pre{font:13px/1.6 ui-monospace,monospace;white-space:pre-wrap;overflow-wrap:anywhere;background:#f0eae0;padding:14px;border-radius:6px}
small{color:#6d6053}.time{font-size:28px;font-weight:600}summary{cursor:pointer}img{max-width:100%;height:auto}
@media(max-width:850px){.grid{grid-template-columns:1fr}main{padding:14px}}
</style><main><h1>__TITLE__</h1>""".replace("__TITLE__", title)
    page += (
        "<p>One recorded case, one new generation per setting. Identical context, image, instructions and schema. "
        "Timing includes the Codex subprocess, response validation and any annotation repair; perception is frozen.</p>"
        f"<p>{first} runs first. The second call may benefit from prompt caching. These two samples cannot establish "
        "a general latency or quality advantage. Fast is requested using the documented CLI flags; the CLI does not "
        "report a server-confirmed processing tier or a credit invoice.</p>"
        f"<p>Checkpoint: <code>{escape(report['checkpoint_id'])}</code> · "
        "<a href='input.json'>Frozen input</a> · <a href='comparison.json'>Comparison record</a></p>"
        + critique + "<div class='grid'>" + "".join(columns) + "</div>"
    )
    if report.get("image_hash"):
        page += "<details><summary>Exact screenshot supplied to both requests</summary><img src='input.png'></details>"
    page += "</main></html>"
    private_write(Path(directory) / "index.html", page)


def run(directory, checkpoint, output, *, comparison="processing"):
    variants = VARIANTS[comparison]
    directory, output = Path(directory), Path(output)
    if output.exists():
        raise ValueError("Use a new output directory; earlier results must be preserved")
    candidates = [
        (session, point)
        for path in sorted((directory / "replays").glob("*.json"))
        for session in [read_json(path)]
        for point in session["checkpoints"]
        if point["id"] == checkpoint
    ]
    if len(candidates) != 1:
        raise ValueError("Expected one recorded checkpoint")
    session, point = candidates[0]
    snapshot = snapshot_for(session, point)
    observations = []
    for observation in snapshot.observations:
        if observation.kind == "screen":
            media = read_json(directory / "images" / (observation.id + ".json"), {}).get("media", {})
            observation = observation.model_copy(update={"image_path": media.get("candidate", "")})
            if observation.image_path and not Path(observation.image_path).is_file():
                raise ValueError("The frozen candidate screenshot is missing")
        observations.append(observation)
    snapshot = replace(snapshot, observations=tuple(observations))
    picture = latest_image(snapshot, True)
    output.mkdir(parents=True)
    if picture:
        private_write(output / "input.png", base64.b64decode(picture))
    prompt = build_prompt(snapshot, "deep", verified_files={})
    write_json(output / "input.json", snapshot.prompt_context())
    report = {
        "checkpoint_id": checkpoint,
        "source_directory": str(directory),
        "release": release_manifest(),
        "input_hash": digest(snapshot.prompt_context()),
        "prompt_hash": digest(SYSTEM + prompt),
        "image_hash": digest(picture) if picture else None,
        "comparison": comparison,
        "model": "gpt-6-astra",
        "order": [mode for mode, _, _ in variants],
        "historical_reference_answers_supplied": False,
        "prior_context": "Frozen app state from the recorded checkpoint, identical in both requests",
        "perception_recomputed": False,
        "human_reviewed": False,
        "results": [],
    }
    write_json(output / "comparison.json", report)
    for mode, reasoning, fast_mode in variants:
        if latest_image(snapshot, True) != picture or digest(snapshot.prompt_context()) != report["input_hash"]:
            raise ValueError("The frozen image or context changed between requests")
        choice = ModelChoice(
            provider="codex", model=report["model"], reasoning=reasoning,
            fast_mode=fast_mode, send_images=True, max_tokens=10000,
        )
        provider = CodexProvider(choice, fast_mode=fast_mode)
        trace = TraceStore(output / "traces" / mode, source="processing-comparison")
        progress = Progress(lambda text: None, trace, request_id=checkpoint, lane="deep")
        row = {"mode": mode, "fast_mode": fast_mode, "model": choice.model_dump(), "delivered": False}
        print(json.dumps({"started": mode, "checkpoint": checkpoint}), flush=True)
        started = time.monotonic()
        try:
            response = provider.generate(snapshot, "deep", Cancellation(), progress)
            row.update(delivered=True, response=response.model_dump(), delivery_notes=response._delivery_notes)
        except Exception as error:
            row["error"] = str(error)
        row["elapsed_seconds"] = round(time.monotonic() - started, 3)
        row["input_unchanged"] = (
            latest_image(snapshot, True) == picture and digest(snapshot.prompt_context()) == report["input_hash"]
        )
        row["raw_response"] = provider.last_raw_response
        row["raw_text"] = provider.last_raw_text
        usage = [e for e in trace.events() if e.get("event") == "provider_usage"]
        row["usage"] = {
            key: sum(e.get(key, 0) for e in usage) if usage and all(key in e for e in usage) else None
            for key in ("input_tokens", "output_tokens", "cached_tokens")
        }
        write_json(output / (mode + ".json"), row)
        report["results"].append(row)
        report["source_unchanged"] = release_manifest()["code_hash"] == report["release"]["code_hash"]
        write_json(output / "comparison.json", report)
        render_report(output, report)
        print(json.dumps({k: row[k] for k in ("mode", "delivered", "elapsed_seconds")}), flush=True)
    report["completed"] = True
    write_json(output / "comparison.json", report)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--comparison", choices=VARIANTS, default="processing")
    args = parser.parse_args()
    run(args.directory, args.checkpoint, args.output, comparison=args.comparison)
