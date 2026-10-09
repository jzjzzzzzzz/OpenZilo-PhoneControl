#!/bin/sh
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
PYTHON="$ROOT/.venv/bin/python"
if [ ! -x "$PYTHON" ]; then PYTHON=python3; fi
EXPECTED=https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl.git
if [ "$(git remote get-url origin)" != "$EXPECTED" ]; then
  echo 'Unexpected origin; publication stopped.' >&2
  exit 1
fi
if [ "$(git branch --show-current)" != main ]; then
  echo 'Publish from main; no automatic branch switching.' >&2
  exit 1
fi
"$PYTHON" scripts/check_release.py
"$PYTHON" -m unittest discover -s tests -q
git fetch origin main
if ! git merge-base --is-ancestor origin/main HEAD; then
  echo 'Remote main has new commits. Integrate them before publishing; no force push.' >&2
  exit 1
fi
"$PYTHON" - <<'PY'
import importlib.util
from pathlib import Path
import subprocess
spec = importlib.util.spec_from_file_location('audit', 'scripts/check_release.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)
files = audit.read_public(Path.cwd())
files[audit.MANIFEST] = Path(audit.MANIFEST).read_bytes()
tracked = set(subprocess.check_output(['git', 'ls-files', '-z']).decode().split('\0')) - {''}
removed = tracked - set(files)
if removed - {'docs/setup-iphone.zh-CN.md'}:
    raise SystemExit('Unreviewed tracked files exist; inspect before publication')
if removed:
    subprocess.run(['git', 'rm', '--cached', '--', *sorted(removed)], check=True)
subprocess.run(['git', 'add', '--', *sorted(files)], check=True)
PY
"$PYTHON" scripts/check_release.py --tracked
if ! git diff --cached --quiet; then
  git commit -m 'Release 1.1: ring IMU workflow, minimal example and USB phone navigation'
fi
git push origin main
echo 'Published: https://github.com/jzjzzzzzzz/OpenZilo-PhoneControl'
