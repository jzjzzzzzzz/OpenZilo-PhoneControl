# Testing and acceptance

## Reproducible automated checks

Install from a source checkout using `./setup.sh`, then run:

```bash
.venv/bin/python -m unittest discover -s tests -v
./ringphone replay
./ringphone test-key --dry-run --delay 0.01
/bin/sh -n ringphone setup.sh
.venv/bin/python -m pip check
```

Tests use synthetic device names, protocol packets, clocks, keyboard APIs and temporary model artifacts. They do not connect to a real ring, post system keys, change accessibility settings or load the user's saved device profile.

Model integration tests additionally require a separate public Motion Lab checkout:

```bash
COMBODIED_MOTION_LAB_PATH=/path/to/combodied-motion-lab \
  .venv/bin/python -m unittest discover -s tests -v
```

Use revision `839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7` for the initial integration baseline. Without the environment variable, the external-runtime tests are explicitly skipped; transport and model-gating tests still run. CI uses pinned public sources on Linux/Python 3.11 and macOS/Python 3.13.

## Initial publication checks

The reviewed snapshot passes **86 tests with no skips** when the external Motion Lab runtime is supplied. Local verification used Python 3.13.14; a clean exported checkout was independently installed with public dependencies under Python 3.14.7. Dependency consistency, shell syntax and offline replay also passed. The CI workflow independently covers the advertised Python 3.11 baseline and macOS Python 3.13.

## Coverage

- Protocol v4 framing, fragmentation, concatenation, invalid packet handling and version rejection.
- System-information handshake, physical identity verification, UUID changes and 20-byte writes.
- Bounded event/IMU queues, stale input, disconnect/cancellation cleanup and reconnect state.
- Duplicate suppression, cooldown, pause/resume, disabled actions and key-release cleanup.
- Configuration, profile import, local persistence and CLI defaults.
- NPZ model validation, overwrite control, hashes, sensor contracts and v1–v4 prediction smoke tests.
- IMU normalization, sequence/timestamp discontinuities and wraparound, idle re-arm and confirmation gates.
- RNN-to-command simulated sessions, slow inference rejection and reporting shutdown.
- Publication allowlist, path/credential checks, symlink rejection, clean export and force-added artifact rejection.

The offline example produces exactly **two simulated commands**. It includes duplicates and stale events to exercise rejection paths.

## What these checks do not establish

Automated tests establish code behavior under the stated fixtures. They do not establish real-device BLE reliability, gesture-recognition accuracy or successful iPhone navigation.

For the initial publication, the actual iPhone/Douyin end-to-end path has **not** been accepted on hardware. No phone success rate or latency measurement is claimed. `posted_keys` is a Mac-side submission count; `iphone_delivery` remains `unverified`.

## Manual acceptance protocol

Follow [iPhone setup](setup-iphone.zh-CN.md) before enabling ring output.

1. Record macOS/iOS versions and ring firmware, without publishing device identifiers.
2. Confirm the Mac-to-iPhone switch/recipe path independently with ten single `test-key` invocations. Record observed swipes, missing actions and extra actions.
3. Validate twenty ring double-press actions and a period of no intentional input.
4. Exercise pause, resume, ring disconnect/reconnect, phone lock, app switching and stop during output.
5. For RNN mode, evaluate representative continuous recordings separately from training data. Compare preprocessing, false activations, missed gestures and latency before enabling keys.
6. Test `previous` only after two independent phone-side switches have been demonstrated. Otherwise keep it disabled.

Keep raw captures, model files, device profiles and local logs outside public issues and releases. A redacted report should include commands, version information, expected behavior, observed behavior and whether the test was simulated or physical.
