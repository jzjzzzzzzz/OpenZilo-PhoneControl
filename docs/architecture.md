# Architecture

## Components

| Module | Responsibility |
| --- | --- |
| `sdk.py` | Load the pinned public OpenZilo SDK; no private sibling folder |
| `ble.py` | BLE discovery, verified sessions, bounded protocol routing and optional IMU reporting |
| `events.py` | Discrete-event interpretation and common command gate |
| `models.py` | Local model import, compatibility checks, Motion Lab adapter and prediction gate |
| `output.py` | macOS Quartz key-down/key-up output; no iPhone delivery acknowledgement |
| `bridge.py` | Session lifecycle, source selection, pause/resume, heartbeat and cleanup |
| `storage.py` | Private local files, atomic JSON writes and instance locks |
| `cli.py` | User-facing commands and explicit output enablement |

## Default event path

The adapter verifies the physical CPUID before enabling event delivery. Firmware events are discrete observations, not RNN predictions. Single-press events are never user-mappable because firmware reserves them for mode switching.

Only system-information requests are sent during an event-mode session. Unexpected audio and IMU traffic is discarded rather than stored. No audio deletion, firmware flashing, clock modification or shipping-mode commands are available through this adapter.

## Optional RNN path

An imported model is loaded before connecting to hardware. After identity verification, the adapter starts IMU reporting and checks the actual sample rate and sensor ranges. Recognition and event paths are mutually exclusive for command output. Model evaluation runs off the asyncio loop; results are discarded if they arrive after pause, reset, disconnect or the freshness deadline.

The command gate operates after prediction confirmation and idle re-arming. Device timestamps are used for stream continuity; they are never compared directly to the host monotonic clock. Sensor reporting is stopped on clean shutdown when possible; BLE disconnection also terminates the firmware's reporting session.

## Output and system boundary

Quartz posts a single Mac key press. Apple Switch Control must already capture that key and route it to an iPhone recipe. The application cannot verify the active phone app or observe the result of the gesture. Status therefore always distinguishes local key submission from unverified phone delivery.

The default is one `SPACE` switch and one next-video action. Multiple keys on the Mac do not establish multiple independent iPhone switches. Two-way navigation requires a separate phone-side acceptance test.

## Storage boundary

`state/` contains bindings, current state and rotated logs. `models/` contains imported weights and manifests. Both are local-only; only `models/README.md` is published. The application does not upload telemetry or models.
