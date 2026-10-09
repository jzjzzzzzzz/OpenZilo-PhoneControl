# USB WebDriverAgent transport

This optional helper connects to an **already signed and running** WebDriverAgent
test on a USB-connected iPhone/iPad. It does not use iPhone Mirroring. Signing,
device trust, developer mode and starting the WDA test are separate prerequisites.
See the [official device setup](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/).

## Check the service without operating the phone

```bash
.venv/bin/python scripts/wda_usb.py --probe
```

The helper selects exactly one USB device and sends only `GET /status` to its
port 8100. It does not create a session, activate an app, tap, swipe or capture
the phone. Device identifiers and pairing credentials are not printed or saved.

## Local forwarding

```bash
.venv/bin/python scripts/wda_usb.py
```

After a recognized WDA status response, it forwards `127.0.0.1:18100` to the
device's port 8100. Leave this terminal and the Xcode WDA test open. Stop the
forwarder with Ctrl-C. It never binds a LAN-facing address. PhoneControl uses this local endpoint to reach WDA. The forwarder itself sends
no UI actions. A device-port failure is not automatically retried.

The client-side protocol is implemented using Python's standard library and
Apple's local usbmuxd service, with reference to
[libusbmuxd](https://github.com/libimobiledevice/libusbmuxd). No additional Homebrew
tool is necessary. Downloaded WDA sources and local build products belong in
ignored `vendor/`, not in the publication snapshot.

## Direct phone commands

Keep the USB forwarder and Xcode WDA test running, and open Douyin on the phone.
From the project directory:

```bash
./ringphone phone next --enable-output
./ringphone phone previous --enable-output
./ringphone phone console --enable-output
```

In the console, type a command and press Enter:

| Command | Operation |
| --- | --- |
| `u` | Upward swipe: next feed item |
| `d` | Downward swipe: previous feed item |
| `q` | Exit the console; do not stop Xcode or the USB forwarder |

Mutation commands are dry-run unless `--enable-output` is provided. For example,
`./ringphone phone console --dry-run` does not access the network or phone.
Explicit dry-run also takes precedence over inspection.

### Local diagnostics

```bash
./ringphone phone inspect
```

Inspection saves a PNG and XML control tree under local-only `state/wda/`. It may
create/reuse a WDA session and temporarily change WDA's screenshot encoding to
PNG, restoring the prior encoding afterwards. It does not click, swipe or change
phone playback. `--capture` on an action saves before/after snapshots; failure
may also save a diagnostic view. Reports distinguish a submitted action from a
subsequent screenshot failure, and never automatically repeat the action.

All HTTP requests use a fixed loopback host, disable proxy routing and redirects,
and have bounded response size/timeouts. Session/element identifiers are checked
before being inserted into paths. Captures and session reports remain private
local artifacts and are not selected by the publication allowlist.


## Ring-to-phone bridge

After importing a personal model:

```bash
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --dry-run
./ringphone run --source rnn --model my-ring --preset rnn --backend wda --enable-output
```

The WDA adapter prepares its connection before BLE input and submits next/previous
swipes outside the BLE event loop. Input generation and freshness checks prevent
old actions from being queued. A submitted request is never automatically retried.
