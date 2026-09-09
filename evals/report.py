"""An inspectable report: inputs, proposals, checks, critiques, and uncertainty."""

import html
import json
from pathlib import Path

from otsc.privacy import atomic_private_write


def write_report(run, directory):
    directory = Path(directory)
    escape = html.escape
    rows = []
    for result in run["results"]:
        state = (
            "pass"
            if result.get("task_passed") is True
            else "fail"
            if result.get("task_passed") is False
            else "needs review"
        )
        payload = {
            k: result.get(k)
            for k in (
                "goal",
                "criteria",
                "observations",
                "files",
                "response",
                "checks",
                "review",
                "inspection_trace",
                "error",
                "raw_response",
            )
        }
        rows.append(
            f'<details><summary><strong class="{state.split()[0]}">{escape(state.upper())}</strong> '
            f"{escape(result['case_id'])} · {escape(result['family'])} · {result.get('elapsed_seconds', 0):.2f}s</summary>"
            f"<pre>{escape(json.dumps(payload, indent=2, ensure_ascii=False))}</pre></details>"
        )
    text = """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Over The Shoulder Coder — evaluation</title><style>
body{font:16px/1.55 system-ui;margin:40px auto;max-width:1100px;padding:0 24px;background:#f8f4ec;color:#30291f}
h1{font-size:30px}details{background:#fffdf9;border:1px solid #cbbba8;border-radius:8px;margin:12px 0;padding:14px}
summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.5 ui-monospace,monospace}
.pass{color:#246244}.fail{color:#9c342c}.needs{color:#805b15}.note{padding:16px;background:#eee3d4;border-radius:8px}
</style><h1>Over The Shoulder Coder: evaluation</h1>"""
    text += f"<p>{escape(run['mode'])} · {escape(run['run_id'])} · corpus {escape(run['corpus']['version'])}</p>"
    text += '<p class="note">Synthetic, assistant-authored cases. Model reviews are provisional. No human calibration, inter-rater agreement, or field success rate is claimed. Unsupported checks remain visible as needs review.</p>'
    text += "<h2>Run summary</h2><pre>" + escape(json.dumps(run["summary"], indent=2)) + "</pre>"
    text += (
        "<h2>Reproduction manifest</h2><details><summary>Code, model, corpus, and rubric versions</summary><pre>"
        + escape(json.dumps(run["manifest"], indent=2))
        + "</pre></details>"
    )
    text += "<h2>Cases</h2>" + "\n".join(rows) + "</html>"
    atomic_private_write(directory / "report.html", text)
    atomic_private_write(directory / "run.json", json.dumps(run, indent=2, ensure_ascii=False))
    return directory / "report.html"
