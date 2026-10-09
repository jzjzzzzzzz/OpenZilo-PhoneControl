# Changelog

## Unreleased

- Finalize IMU datasets only after successful capture; retain interrupted samples as private `.partial` files.
- Preserve capture errors and cancellation reports when connection cleanup fails.
- Validate WDA timeouts and input deadlines; handle truncated HTTP responses without replaying actions.
- Stop follow-up WDA initialization requests after cancellation and retain startup failure reports.
- Preserve screenshot errors when restoring screenshot settings fails.

## 1.1.0

- Add USB WDA next/previous control and an interactive phone console.
- Connect model-driven ring actions to the asynchronous WDA output adapter.
- Add labeled ring-imu/v1 collection and a pinned v3 personal-training entry point.
- Publish the minimal synthetic GRU example, model card, input windows and exported predictions.
- Add export/import, native event-object and IMU-to-WDA integration tests.
- Update English/Chinese quick starts and exact publication auditing for the example model.

## 1.0.0

- Introduce verified OpenZilo BLE sessions, firmware event gates and local model import.
- Add macOS output adapters, replay, diagnostics, CI and the publication inventory.
