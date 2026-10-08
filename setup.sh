#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT"
PYTHON=${RINGPHONE_PYTHON:-python3}
"$PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "需要 Python 3.11 或更新版本"'
if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install -e '.[rnn]'
./ringphone doctor
