# Troubleshooting

| Symptom | Check |
| --- | --- |
| Ring not found | Power it on, keep it near the Mac, disconnect another ring app, then run `scan` |
| `device busy` while starting IMU | Enter gesture mode and stop any other recording/inference session |
| Too few IMU frames | Check gesture mode and BLE signal; rerun the labeled capture |
| WDA port refused | Keep the Xcode WDA test running and the phone connected by USB |
| Wrong foreground app | Open Douyin before sending a phone command |
| Model sensor mismatch | Use the sampling rate and ranges from the capture report when importing |
| No phone action in a bridge session | Check `--enable-output`, idle re-arming and the model probabilities |
| Artifact checksum changed | Reimport the intended model and matching runtime checkout |

## Useful commands

```bash
./ringphone doctor
./ringphone status
./ringphone model inspect my-ring
.venv/bin/python scripts/wda_usb.py --probe
./ringphone phone inspect
```

Inspect source and screenshots under local `state/wda/`. A cancelled client request
may have reached WDA; check the phone before issuing the same action again.
