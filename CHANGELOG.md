# Changelog

## 1.0.0 — Initial public snapshot

- Introduce OpenZilo-PhoneControl, a local ring-to-iPhone bridge through macOS Switch Control.
- Support verified BLE sessions, firmware button/gesture input, controlled key output and dry-run replay.
- Use the public OpenZilo SDK at a reviewed pinned revision.
- Add local Motion Lab NPZ import, artifact validation and optional continuous RNN inference.
- Separate training documentation and runtime sources from the application; distribute no trained model weights.
- Document iPhone setup, manual acceptance requirements, model preprocessing and publication boundaries.
- Add synthetic tests, public CI and an exact release-file allowlist with checksums.

Phone-side execution and model recognition quality require independent physical/data evaluation; this initial snapshot does not claim completed end-to-end hardware acceptance.
