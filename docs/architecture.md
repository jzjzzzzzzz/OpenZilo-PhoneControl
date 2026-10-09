# Architecture

## Components

| Module | Responsibility |
| --- | --- |
| `ble.py` | Verified NUS transport, bounded queues and IMU reporting |
| `collect.py` | Labeled local ring-imu/v1 capture |
| `models.py` | NPZ import, runtime validation, windows and recognition gate |
| `events.py` | Discrete-event mapping, freshness, duplicate suppression and cooldown |
| `wda.py` | Douyin application checks, native swipes and async bridge adapter |
| `storage.py` | Private local files, atomic status and instance locks |
| `cli.py` | Collection, model commands, direct phone control and bridge lifecycle |

## Input and inference

BLE sessions verify the ring's physical identity before accepting input.
Firmware events and IMU-model input are separate sources. For RNN input, six raw
channels are normalized by 32768, resampled according to the model contract and
classified locally. Idle re-arms the recognition gate; repeated confident
predictions produce `next` or `previous` actions.

## Phone output

The WDA backend uses the existing USB forwarder on `127.0.0.1:18100`. It verifies
WDA readiness and the Douyin bundle before submitting a native swipe. Network
requests run outside the BLE event loop. Pause/session changes revoke requests
that have not reached WDA; submitted requests are not automatically replayed.

Direct `phone` commands use the same WDA client without connecting the ring.
Local status separates accepted API requests from observed phone outcomes.
The keyboard and Mirroring adapters remain available as alternative Mac outputs.

## Data and model locations

- `captures/`: user recording sessions and sensor reports.
- `models/`: imported personal exports and runtime manifests.
- `state/`: device binding, status and private phone diagnostics.
- `vendor/`: locally obtained WDA source, builds and signing-related project data.
- `demo/`: the published synthetic model, sample windows and inference results.

The publication snapshot is selected by an exact allowlist rather than copying
these local directories.
