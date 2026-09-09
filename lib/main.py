"""Compatibility launcher for the unified Over The Shoulder Coder application."""

if __name__ == "__main__":
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from ots import main

    main()
