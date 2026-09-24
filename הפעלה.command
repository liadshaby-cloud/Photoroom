#!/bin/zsh
set -e
cd "$(dirname "$0")"
trap 'echo; read "?לחצו Enter לסגירה"' EXIT
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import ApplicationServices, Quartz, AppKit' 2>/dev/null; then
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
fi
echo 'תנו שם לאוסף. כדי להמשיך הרצה קודמת השתמשו באותו שם.'
read 'collection_name?שם האוסף: '
if [[ -z "$collection_name" ]]; then
  echo 'נדרש שם לאוסף.'
  exit 1
fi
.venv/bin/python -u downloader.py --session "$collection_name"
