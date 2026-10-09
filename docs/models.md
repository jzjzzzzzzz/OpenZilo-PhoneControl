# Model integration

## Portable export

PhoneControl accepts Motion Lab's `ring-rnn-v1` NPZ format. The export includes
class labels, weights, feature statistics, preprocessing version, window length
and resampling method. Runtime import checks finite arrays, class uniqueness,
archive bounds and an inference smoke test. Pickle/object arrays are not used.

The provided workflow trains v3 features from six raw IMU channels. A matching
Motion Lab checkout is selected explicitly by `--lab` and recorded by checksum.

## Personal training

Collect with `ringphone record`, then use
[`scripts/train_ring_model.py`](../scripts/train_ring_model.py). It runs the pinned
Motion Lab trainer with matching raw-input preparation. Dataset format and
session-based evaluation are described in
[Motion Lab](https://github.com/jzjzzzzzzz/combodied-motion-lab/blob/main/docs/training.md).

## Import

```bash
./ringphone model import models/my-ring.npz --name my-ring \
  --lab ../combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000
./ringphone model inspect my-ring
```

Use the sensor values from your collection report. Files are copied into the
local model directory with a manifest containing artifact and runtime hashes.
Existing names require an explicit `--replace`.

## Recognition and output

```bash
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --dry-run
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --enable-output
```

The ring reports IMU in gesture mode. Windows follow the exported sampling and
preprocessing contract. Sequence/timestamp discontinuities reset recognition.
The gate requires confident idle re-arming and repeated matching gesture
predictions. `up` maps to next; `down` maps to previous.

## Bundled example

[The small example](../demo/README.md) is trained on synthetic IMU and has visible
export/import results. Reproduce with `scripts/demo_imu.py`. Live phone output
uses a personal model trained on the wearer's own recordings.

Personal models and manifests remain local. Only the named synthetic example is
included in the publication inventory.
