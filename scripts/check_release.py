#!/usr/bin/env python3
"""Exact publication allowlist, text audit and reproducible SHA-256 inventory."""
from __future__ import annotations

import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "release-files.sha256"
# Adding a publishable file is an explicit review decision, not a directory glob.
PUBLIC_FILES = frozenset("""
.gitignore
.github/repository.json
.github/workflows/ci.yml
.github/pull_request_template.md
CHANGELOG.md
CONTRIBUTING.md
PUBLIC_RELEASE.md
README.md
README.zh-CN.md
SECURITY.md
THIRD_PARTY_NOTICES.md
pyproject.toml
requirements.txt
ringphone
setup.sh
docs/architecture.md
docs/models.md
docs/mirroring.md
docs/testing.md
docs/troubleshooting.md
docs/wda.md
docs/validation.md
docs/quickstart.zh-CN.md
demo/README.md
demo/imu-baseline.npz
demo/model-card.json
demo/imu-windows.json
demo/predictions.json
examples/button.json
examples/events.jsonl
examples/gesture.json
examples/rnn.json
examples/two-switches.json
models/README.md
ring_iphone/__init__.py
ring_iphone/__main__.py
ring_iphone/ble.py
ring_iphone/bridge.py
ring_iphone/cli.py
ring_iphone/config.py
ring_iphone/collect.py
ring_iphone/events.py
ring_iphone/models.py
ring_iphone/mirroring.py
ring_iphone/output.py
ring_iphone/sdk.py
ring_iphone/storage.py
ring_iphone/wda.py
scripts/check_release.py
scripts/wda_usb.py
scripts/train_demo_model.py
scripts/train_ring_model.py
scripts/demo_imu.py
scripts/publish.sh
tests/fakes.py
tests/test_ble.py
tests/test_core.py
tests/test_models.py
tests/test_mirroring.py
tests/test_pairing.py
tests/test_release.py
tests/test_rnn_runtime.py
tests/test_runtime.py
tests/test_wda_usb.py
tests/test_wda.py
tests/test_demo.py
tests/test_collect.py
""".split())
EXECUTABLES = {"ringphone", "setup.sh", "scripts/publish.sh"}
BINARY_FILES = {"demo/imu-baseline.npz"}
FORBIDDEN = (
    ("personal absolute path", re.compile(r"/(?:Users|home)/[A-Za-z0-9_.-]+")),
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("GitHub credential", re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,})")),
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Bluetooth MAC address", re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")),
    ("CoreBluetooth UUID", re.compile(r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b")),
)


def audit_text(name: str, data: bytes):
    if len(data) > 256 * 1024 or b"\0" in data:
        raise ValueError(f"Non-text or oversized publication file: {name}")
    text = data.decode("utf-8")
    for label, pattern in FORBIDDEN:
        if pattern.search(text):
            raise ValueError(f"{label} found in {name}; redact before publication")


def read_public(root: Path):
    result = {}
    for name in sorted(PUBLIC_FILES):
        path = root / name
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError(f"Symlink is not publishable: {name}")
        if not path.is_file():
            raise ValueError(f"Missing reviewed file: {name}")
        data = path.read_bytes()
        if name in BINARY_FILES:
            audit_demo_model(root, data)
        else:
            audit_text(name, data)
        result[name] = data
    return result


def audit_demo_model(root: Path, data: bytes):
    import numpy as np
    card_path = root / 'demo/model-card.json'
    if card_path.is_symlink():
        raise ValueError('Demo model card must not be a symlink')
    card = json.loads(card_path.read_text())
    if len(data) > 65536 or hashlib.sha256(data).hexdigest() != card.get('model_sha256'):
        raise ValueError('Demo model bytes/hash do not match its reviewed card')
    if card.get('data_source') != 'synthetic_imu_only':
        raise ValueError('Only the synthetic example may be published')
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        entries = archive.infolist()
        if len(entries) > 40 or len({entry.filename for entry in entries}) != len(entries) or sum(entry.file_size for entry in entries) > 262144:
            raise ValueError('Demo archive exceeds limits')
    with np.load(io.BytesIO(data), allow_pickle=False) as arrays:
        for key in arrays.files:
            value = arrays[key]
            if value.dtype.hasobject:
                raise ValueError('Object arrays are not allowed')
            if np.issubdtype(value.dtype, np.number) and not np.isfinite(value).all():
                raise ValueError('Demo parameters must be finite')
            if value.dtype.kind in 'US':
                audit_text('demo model string metadata', str(value.tolist()).encode())
        metadata = json.loads(str(arrays['metadata']))
        if metadata.get('demo_only') is not True or metadata.get('training_data_source') != 'synthetic_imu_only':
            raise ValueError('Demo provenance is required')
        if str(arrays['model_type']) != 'ring-rnn-v1' or arrays['classes'].tolist() != ['down', 'idle', 'up']:
            raise ValueError('Unexpected demo model contract')


def inventory(files: dict[str, bytes]):
    return "".join(f"{hashlib.sha256(data).hexdigest()}  {name}\n" for name, data in sorted(files.items()))


def check_tracked(root: Path, files: dict[str, bytes]):
    records = subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=root).split(b"\0")
    seen = set()
    for record in filter(None, records):
        metadata, raw_name = record.split(b"\t", 1)
        mode, _, stage = metadata.decode().split()
        name = raw_name.decode("utf-8")
        if name not in files or stage != "0":
            raise ValueError(f"Unreviewed or conflicted tracked file: {name}")
        expected_mode = "100755" if name in EXECUTABLES else "100644"
        if mode != expected_mode:
            raise ValueError(f"Unexpected tracked mode {mode}: {name}")
        staged = subprocess.check_output(["git", "show", f":{name}"], cwd=root)
        if staged != files[name]:
            raise ValueError(f"Index differs from audited working file: {name}; stage reviewed changes")
        seen.add(name)
    if seen != set(files):
        raise ValueError(f"Reviewed files are not all tracked: {sorted(set(files) - seen)}")


def export_public(destination: Path, files: dict[str, bytes]):
    destination.mkdir(parents=True, exist_ok=False)
    for name, data in files.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o755 if name in EXECUTABLES else 0o644)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-manifest", action="store_true")
    parser.add_argument("--tracked", action="store_true", help="also audit Git index, names and modes")
    parser.add_argument("--export", type=Path, help="create a new clean directory from reviewed files only")
    args = parser.parse_args(argv)
    files = read_public(ROOT)
    expected = inventory(files)
    path = ROOT / MANIFEST
    if path.is_symlink():
        raise ValueError("Publication manifest cannot be a symlink")
    if args.write_manifest:
        path.write_text(expected, encoding="utf-8")
        path.chmod(0o644)
    if not path.is_file() or path.read_text(encoding="utf-8") != expected:
        raise ValueError("Manifest missing or stale; review changes, then use --write-manifest")
    files[MANIFEST] = expected.encode()
    if args.tracked:
        check_tracked(ROOT, files)
    if args.export:
        export_public(args.export, files)
    print(f"Release audit passed: {len(files)} reviewed files; only the allowlisted synthetic demo model, no private state.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        raise SystemExit(f"Release audit failed: {error}")
