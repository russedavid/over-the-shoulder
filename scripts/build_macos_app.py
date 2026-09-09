"""Build a local development .app around this checkout's Python environment."""

import argparse
import plistlib
import shlex
import shutil
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    python = root / ".venv/bin/python"
    if not python.exists():
        parser.error("Run uv sync --python 3.13 --extra mac first")
    app = args.output or root / "dist/Over The Shoulder Coder.app"
    contents = app / "Contents"
    binaries = contents / "MacOS"
    binaries.mkdir(parents=True, exist_ok=True)
    executable = binaries / "OverTheShoulderCoder"
    codex = shutil.which("codex")
    extra_path = ":".join(
        filter(None, [str(Path(codex).parent) if codex else "", "/opt/homebrew/bin", "/usr/local/bin"])
    )
    executable.write_text(
        "#!/bin/sh\nset -eu\n"
        + "cd "
        + shlex.quote(str(root))
        + "\n"
        + "export PATH="
        + shlex.quote(extra_path)
        + ':"$PATH"\n'
        + "exec "
        + shlex.quote(str(python))
        + ' -m otsc "$@"\n'
    )
    executable.chmod(0o755)
    info = {
        "CFBundleName": "Over The Shoulder Coder",
        "CFBundleDisplayName": "Over The Shoulder Coder",
        "CFBundleIdentifier": "dev.overtheshouldercoder.desktop",
        "CFBundleVersion": "1",
        "CFBundleShortVersionString": "0.1.0",
        "CFBundleExecutable": "OverTheShoulderCoder",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "13.0",
        "NSHighResolutionCapable": True,
        "NSMicrophoneUsageDescription": "Use your speech as context for the task you choose to work on.",
        "NSScreenCaptureUsageDescription": "Read the screen and system audio you enable as task context.",
    }
    (contents / "Info.plist").write_bytes(plistlib.dumps(info))
    print(app)


if __name__ == "__main__":
    main()
