"""Explicit commands for the private recorded-input evaluation workflow."""

import argparse
import concurrent.futures
import json
from pathlib import Path

from evals.recording_data import build_index, default_directory, read_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=["index", "audio", "ocr", "vision", "replay", "references", "assess"])
    parser.add_argument("--directory", default=str(default_directory()))
    parser.add_argument("--source", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--role", choices=["candidate", "reference"], default="candidate")
    parser.add_argument("--session", help="Limit session work to one indexed session ID")
    parser.add_argument("--workers", type=int, choices=[1, 2], default=1)
    args = parser.parse_args()
    directory = Path(args.directory)
    if args.stage == "index":
        if (directory / "index.json").exists():
            parser.error("This directory already contains an index. Use a new directory for a new recording version.")
        index = build_index(args.source, directory)
        print(
            json.dumps(
                {"directory": str(directory), "sessions": len(index["sessions"]), "assets": len(index["assets"])}
            )
        )
        return
    from evals.recording_components import assess_images, audio_pass, image_pass
    from evals.recording_replay import assess_session, reference_session, replay_session

    if args.stage == "audio":
        return audio_pass(directory, reference=args.role == "reference")
    if args.stage in {"ocr", "vision"}:
        return image_pass(directory, "ocr" if args.stage == "ocr" else args.role)
    index = read_json(directory / "index.json")
    sessions = [s for s in index["sessions"] if not args.session or s["id"] == args.session]
    if not sessions:
        parser.error("The selected session is not in this recording index")
    action = {"replay": replay_session, "references": reference_session, "assess": assess_session}[args.stage]
    if args.stage == "assess" and not args.session:
        assess_images(directory)
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for _ in pool.map(lambda session: action(directory, session["id"]), sessions):
            pass


if __name__ == "__main__":
    main()
