# Validation results

## Example model

| Check | Result |
| --- | --- |
| Training source | Generated six-axis IMU, independent train/validation/test seeds |
| Model | One-layer GRU, v3 input features, 1,692 parameters |
| Export size | 12,777 bytes |
| Synthetic test windows | 30 / 30 classified correctly |
| PyTorch / NumPy probability difference | Maximum 1.02 × 10⁻⁷ |
| NPZ import | Passed archive, parameter, sensor-contract and runtime validation |
| Exported sample outputs | down → previous; idle → no action; up → next |

The weights and reproducible input/output files are in [demo/](../demo/README.md).
The model is a format/inference example; personal recordings are used for actual
wearer training.

## Application checks

The test suite covers BLE framing and identity checks, labeled capture,
export/import, window processing, idle re-arming, command gates, native output
bindings, WDA routing and publication auditing. The IMU-to-WDA integration test
uses the bundled model and a simulated WDA endpoint.

USB WDA `/status` and the Douyin bundle were checked on the connected iPhone 14.
Device API feed navigation was exercised through the user's terminal. Source,
model and fixture validation can be repeated offline:

```bash
COMBODIED_MOTION_LAB_PATH=../combodied-motion-lab \
  .venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/demo_imu.py --lab ../combodied-motion-lab
.venv/bin/python scripts/check_release.py
```

## Publication inventory

The allowlist includes one synthetic NPZ, its model card and generated example
windows. Personal models, captures, signing materials, phone screenshots and
runtime state are excluded. The SHA-256 inventory is `release-files.sha256`.
