# Local RNN model integration

[README](../README.md) / Model integration

## Responsibility split

Training, dataset preparation, subject/session-aware evaluation and model export belong in [ComBodied Motion Lab](https://github.com/jzjzzzzzzz/combodied-motion-lab). Start with its [training guide](https://github.com/jzjzzzzzzz/combodied-motion-lab/blob/main/docs/training.md). This application supplies the import and live-device adapter, not a separate training implementation.

The initially reviewed Motion Lab revision is `839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7`. Obtain it separately:

```bash
git clone https://github.com/jzjzzzzzzz/combodied-motion-lab.git /path/to/combodied-motion-lab
git -C /path/to/combodied-motion-lab checkout 839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7
```

`--lab` loads Python code from the selected local checkout. Select a checkout you intend to execute. No training source or weights are vendored by this project.

## Accepted export

- NPZ, model type `ring-rnn-v1`; the upstream `RNNGestureClassifier.save()` format.
- Two to 64 unique labels, including `idle`.
- Positive feature standard deviations and finite numerical parameters.
- Declared `resample_method`: `anti_alias_bin_average_v1` or `linear_interpolation_v1`.
- Feature version v1, v2 or v3; v4 requires explicit opt-in below.
- A valid `predict_proba()` smoke test before import completes.
- No pickle/object arrays. Both file and expanded NPZ size are bounded at 128 MiB.

PyTorch checkpoints (`.pt`, `.pth`) are not accepted directly. Export the portable model in Motion Lab first.

## Import

From the PhoneControl repository root:

```bash
./ringphone model import /path/to/export.npz \
  --name gesture-rnn \
  --lab /path/to/combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000
```

The three sensor values are required rather than inferred. They describe the data used to train the model. On connection, the actual device-reported sampling rate and ranges must match; a mismatch stops the RNN session.

The command creates:

```text
models/gesture-rnn.npz    # local weights; ignored by Git
models/gesture-rnn.json   # local manifest, hashes and runtime path; ignored by Git
```

Use `--replace` to explicitly replace a name. Interrupted imports fail closed on the next checksum check. `--models-dir PATH` may redirect both import and runtime lookup.

```bash
./ringphone model inspect gesture-rnn
```

The manifest stores model and key runtime-source hashes. If weights or the selected runtime files change, reimport and revalidate; the application does not silently switch a model implementation. The hashes detect changes, not the trustworthiness of a model or source checkout.

## Live recognition

```bash
./ringphone run --source rnn --model gesture-rnn --preset rnn --dry-run
# After physical iPhone acceptance:
./ringphone run --source rnn --model gesture-rnn --preset rnn --enable-output
```

Put the ring in gesture mode first. The adapter requests IMU reporting, maintains a bounded window, and normalizes channels as:

```text
[accel_x, accel_y, accel_z, gyro_x, gyro_y, gyro_z] / 32768
```

It uses the model's window length, stride, target steps and the Motion Lab resampler. Sequence gaps, timestamp discontinuities, stale batches and reconnection reset the window and the recognition gate. High-confidence idle must re-arm the gate before another command. Recognition requires repeated matching predictions plus confidence and probability-margin checks, followed by the shared command cooldown.

The `rnn` preset maps `up` to `next` and `down` to `previous`. Models trained with other labels need an explicit configuration, for example:

```json
{
  "mapping": {"wave": "next", "rotate_front": "next"},
  "keys": {"next": "SPACE", "previous": null},
  "rnn_confidence": 0.92,
  "rnn_margin": 0.12,
  "rnn_confirmations": 2,
  "rnn_idle_confidence": 0.8
}
```

Use `--config path/to/config.json` instead of `--preset rnn`. The previous action remains disabled until the phone-side second switch is verified.

The current mapping schema accepts `up`, `down`, `left`, `right`, `wave`, `rotate_front`, `rotate_back`, `double_tap` and `key_double_press`. Other model labels may remain unmapped; add a reviewed schema extension before assigning them to output. Neither `idle` nor the firmware mode-switch single press can trigger an action.

## v4 preprocessing is an explicit compatibility choice

Motion Lab v4 expects 22 precomputed channels rather than six raw channels. This adapter can call Motion Lab's `euler_displacement_features()` for each live window using the declared sensor parameters:

```bash
./ringphone model import /path/to/v4-export.npz \
  --name gesture-v4 --lab /path/to/combodied-motion-lab \
  --sample-rate 100 --accel-range 16 --gyro-range 2000 \
  --allow-window-v4
```

The upstream trainer may compute these features over a full session before windowing. Reinitializing orientation, bias and displacement within each live window is **not generally identical** to that session-wide procedure. The opt-in therefore enables an experimental integration path, not a guarantee of equivalent preprocessing or recognition quality. Validate it on representative continuous recordings or use a matched preprocessing pipeline before enabling real output.

## Publication boundary

Do not attach real weights, raw captures or model manifests to issues or releases. Tests create tiny synthetic weights in temporary directories. The release allowlist and tracked-file audit reject model artifacts regardless of Git ignore rules.
