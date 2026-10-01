#!/bin/zsh
set -e
cd "$(dirname "$0")"
trap 'echo; read "?לחצו Enter לסגירה"' EXIT
if [[ ! -x .venv-bulk/bin/python ]]; then
  python3 -m venv .venv-bulk
fi
.venv-bulk/bin/python -m pip install --disable-pip-version-check -r bulk/requirements.txt
.venv-bulk/bin/python -u bulk/runner.py "$@"
