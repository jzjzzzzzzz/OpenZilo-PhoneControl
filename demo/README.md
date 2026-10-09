# IMU model example

A small trained model and three six-axis windows for the complete NPZ import and
recognition-export workflow. The example uses synthetic signals; train your own
model on ring recordings for actual use.

## Files

| File | Purpose |
| --- | --- |
| `imu-baseline.npz` | Portable one-layer GRU, hidden size 8, v3 features |
| `model-card.json` | Training settings, evaluation and SHA-256 |
| `imu-windows.json` | down / idle / up, 160 signed-int16 frames per window |
| `predictions.json` | Imported-model probabilities and mapped actions |

Sensor contract: **100 Hz, ±16 g, ±2000 degrees/s**. Windows span 1.6 seconds and
are anti-alias resampled to 40 steps. Channel order is acceleration X/Y/Z, then
angular velocity X/Y/Z; raw values are divided by 32768.

## Reproduce inference

```bash
.venv/bin/python scripts/demo_imu.py --lab ../combodied-motion-lab \
  --output results/imu-demo.json
```

Expected predictions: down → previous, idle → no command, up → next.
No BLE connection or phone action is made by this example.

## Rebuild the example model

In a Python environment with NumPy and PyTorch:

```bash
python scripts/train_demo_model.py --lab ../combodied-motion-lab \
  --output-dir results/rebuilt-demo
```

The generator uses separate seeds for 90 training, 30 validation and 30 test
windows. The exported model has 1,692 parameters. The checked-in model scored
30/30 on its synthetic test windows and passed PyTorch/NumPy inference parity.
Personal training and recording-session evaluation are covered in
[the workflow guide](../docs/quickstart.zh-CN.md).
