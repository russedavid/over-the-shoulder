#!/bin/zsh
set -eu
cd -- "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  print 'First run: uv sync --python 3.13 --extra mac'
  exit 1
fi
exec .venv/bin/python -m otsc "$@"
