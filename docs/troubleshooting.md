# Troubleshooting

| Symptom | Check |
| --- | --- |
| No ring found | Power, distance, Bluetooth permission, and whether another program already owns the connection |
| CPUID mismatch | Confirm the intended ring; use explicit `pair --replace` only when changing devices |
| Mac key posts but iPhone does not swipe | Verify the Mac-to-iPhone switch connection and the selected phone recipe before debugging ring recognition |
| `action-disabled` | The action has no configured key; previous-video is intentionally disabled by default |
| `settling`, `cooldown`, `duplicate` or `stale` | The common command gate suppressed a noneligible event |
| RNN start reports device busy | Enter firmware gesture mode before starting IMU reporting |
| RNN sensor-contract mismatch | Check training sampling rate and sensor ranges; do not silently rescale a trained model |
| No repeated RNN commands | A confident idle prediction is required to re-arm after each accepted motion |
| Model/runtime checksum mismatch | Reimport from the intended source and repeat validation |
| v4 import rejected | Read the window-local preprocessing section in [models](models.md) before opting in |

Use `./ringphone doctor`, `./ringphone status`, and local `state/bridge.log` for diagnosis. Remove device identifiers, filesystem paths and model metadata before sharing excerpts. Logs are not phone-execution receipts.
