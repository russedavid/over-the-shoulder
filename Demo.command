#!/bin/zsh
set -eu
cd -- "${0:A:h}"
exec './Over The Shoulder Coder.command' --demo "$@"
