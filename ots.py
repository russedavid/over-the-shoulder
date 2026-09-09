"""Run Over The Shoulder Coder with: python ots.py"""

import os
import sys
from pathlib import Path


def main():
    root = Path(__file__).resolve().parent
    environment = root / ".venv"
    python = environment / "bin/python"
    if python.exists() and Path(sys.prefix).resolve() != environment.resolve():
        os.execv(str(python), [str(python), str(root / "ots.py"), *sys.argv[1:]])
    from otsc.__main__ import main as run

    run()


if __name__ == "__main__":
    main()
