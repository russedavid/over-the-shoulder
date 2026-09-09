"""Import-safe launcher. Capture and model calls require explicit UI actions."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Over The Shoulder Coder — one task, useful assistance")
    parser.add_argument("--demo", action="store_true", help="Synthetic replay; no capture, API keys, or model calls")
    parser.add_argument("--design-demo", action="store_true", help="Start the synthetic system-design example")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="Exercise native views with synthetic data and exit")
    commands = parser.add_subparsers(dest="command")
    evaluate = commands.add_parser("eval", help="Run task-specific evaluations; live inference is explicit")
    evaluation_mode = evaluate.add_mutually_exclusive_group()
    evaluation_mode.add_argument("--live", action="store_true")
    evaluation_mode.add_argument(
        "--replay", metavar="RUN_DIRECTORY", help="Recheck recorded development outputs; preserve the original run"
    )
    evaluate.add_argument("--split", choices=["development", "holdout", "all"], default="development")
    evaluate.add_argument("--limit", type=int)
    evaluate.add_argument("--cases", help="Comma-separated case IDs within the selected split")
    evaluate.add_argument("--inspection", action="store_true")
    evaluate.add_argument("--no-judge", action="store_true")
    evaluate.add_argument("--model", default="gpt-5.5")
    evaluate.add_argument("--judge-model", default="gpt-6-astra")
    evaluate.add_argument("--output")
    evaluate.add_argument("--reference-check", help="Reuse a compatible saved judge reference check")
    operations = commands.add_parser("ops", help="Inspect local operational metadata")
    operations.add_argument("--output")
    operations.add_argument("--recovery-exercise", action="store_true")
    release = commands.add_parser("export-source", help="Prepare a clean source review archive without publishing")
    release.add_argument("--output")
    args = parser.parse_args()
    if args.command == "eval":
        import json
        from pathlib import Path

        from evals.runner import run_live, validate_corpus

        if args.replay:
            if not args.reference_check or not args.output:
                parser.error("--replay requires --reference-check and a new --output directory")
            from evals.replay import replay_run

            reference = json.loads(Path(args.reference_check).read_text())
            run, path = replay_run(args.replay, args.output, reference)
            print(json.dumps({"report": str(path), **run["summary"]}))
        elif args.live:
            if args.limit is not None and args.limit <= 0:
                parser.error("--limit must be positive")
            reference = json.loads(Path(args.reference_check).read_text()) if args.reference_check else None
            run_live(
                split=args.split,
                limit=args.limit,
                inspection=args.inspection,
                judge=not args.no_judge,
                model=args.model,
                judge_model=args.judge_model,
                output=args.output,
                reference_check=reference,
                case_ids=args.cases.split(",") if args.cases else None,
            )
        else:
            print(json.dumps({"corpus_valid": True, **validate_corpus(), "live_inference": False}, indent=2))
        return
    if args.command == "ops":
        import json

        from otsc.operations import recovery_exercise, report

        if args.recovery_exercise:
            from otsc.privacy import app_directory

            print(json.dumps(recovery_exercise(args.output or app_directory() / "recovery-exercise"), indent=2))
        else:
            print(json.dumps(report(output=args.output), indent=2))
        return
    if args.command == "export-source":
        from otsc.release import export_source

        print(export_source(args.output))
        return
    if sys.platform != "darwin":
        parser.error("The desktop app requires macOS. Core tests can run on other platforms.")
    from otsc.app import run

    run(args)


if __name__ == "__main__":
    main()
