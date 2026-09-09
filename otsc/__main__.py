"""Import-safe launcher. Capture and model calls require explicit UI actions."""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(description="Over The Shoulder Coder — one task, useful assistance")
    parser.add_argument("--demo", action="store_true", help="Synthetic replay; no capture, API keys, or model calls")
    parser.add_argument("--design-demo", action="store_true", help="Start the synthetic system-design example")
    parser.add_argument("--smoke-test", metavar="OUTPUT_DIR", help="Exercise native views with synthetic data and exit")
    args = parser.parse_args()
    if sys.platform != "darwin":
        parser.error("The desktop app requires macOS. Core tests can run on other platforms.")
    from otsc.app import run

    run(args)


if __name__ == "__main__":
    main()
