# Publication boundary

The public snapshot contains application source, operational guides, tests and
one named synthetic IMU example.

## Included assets

- Public OpenZilo dependency, pinned to a reviewed commit.
- BLE collection, model import/inference and phone-control source.
- WDA USB forwarding, next/previous commands and console.
- `demo/imu-baseline.npz`, its model card, generated inputs and exported predictions.
- Reproducible example generation and a pinned Motion Lab training entry point.
- CI and a SHA-256 publication inventory.

## Local files

`captures/`, `state/`, `results/`, `vendor/` and personal `models/` exports remain
local. They hold sensor recordings, device identities, logs, phone screenshots,
WDA builds, signing materials and personal model weights. The only binary model
in the allowlist is `demo/imu-baseline.npz`, trained on synthetic data.

## Audit and export

```bash
.venv/bin/python scripts/check_release.py --write-manifest
.venv/bin/python scripts/check_release.py
.venv/bin/python scripts/check_release.py --export /path/to/new-public-export
```

The auditor checks exact paths, symlinks, text metadata, common credential/device
identifier patterns and checksums. The demo NPZ additionally has bounded archive
size, finite numeric arrays, no object/pickle arrays and synthetic-only provenance.
`release-files.sha256` covers every published file except itself.

After staging the exact reviewed set, `--tracked` also verifies Git index content
and modes. Repository: `jzjzzzzzzz/OpenZilo-PhoneControl`; topics include
`combodied-ai` and `openzilo`.
