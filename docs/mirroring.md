# iPhone Mirroring output

This optional, experimental backend maps accepted ring actions to scroll-wheel
input in Apple's **iPhone Mirroring** app. The original Switch Control backend
remains the default. Neither backend claims that local event submission proves
a successful phone action.

## Source review

The implementation was independently written after reviewing these public
repositories; none of their binaries, scripts, or dependencies are executed:

| Reference | Reviewed revision | Relevant finding |
| --- | --- | --- |
| [iphone-mcp](https://github.com/yev-yev-yev/iphone-mcp/blob/e56e78e281a4c564a28c82e3cfa481411c647380/src/lib/mirroring/mirroring-client.ts) | `e56e78e281a4c564a28c82e3cfa481411c647380` | HID wheel bursts; avoid synthetic touch-phase overrides |
| [iphoneclaw](https://github.com/NoEdgeAI/iphoneclaw/blob/fb38f85d10801b17e87614be16dad5e5ea1cb6b5/iphoneclaw/macos/input_mouse.py) | `fb38f85d10801b17e87614be16dad5e5ea1cb6b5` | Move the global pointer before wheel input; bounded bursts |

The first repository's comment describing unit `1` as pixels was checked against
Apple's CoreGraphics headers: **pixel units are `0`; line units are `1`**. This
implementation uses the actual pixel constant, not that comment. Natural-scroll
direction is read without changing system preferences; an explicit inversion
option supports local calibration. External results are not this project's
hardware acceptance results.

The native binding uses the six-fixed-argument `CGEventCreateScrollWheelEvent2`
variant. A previous three-argument `ctypes` declaration of the variadic function
lost the wheel delta on Apple arm64. A real, non-posting event-object regression
now verifies signed pixel deltas. Pixel units may set `isContinuous` internally;
"unphased" here means the adapter does not synthesize touch-phase or momentum
fields, not that every event is classified as discrete.

## Requirements

- macOS with iPhone Mirroring already configured and connected to the target phone.
- Existing Accessibility permission for the process running the bridge.
- A visible, unique, portrait Mirroring window; Douyin open on its video feed.
- Mirroring stays in the foreground while commands are being sent.

The application does not pair Apple Accounts, unlock the phone, change privacy
permissions, or install an iPhone application. It does not need Screen Recording
permission because this backend does not capture the screen. The active iPhone
app and connection state must be checked visually before enabling output.

## Simulate before connecting the ring

```bash
# Offline: gate one synthetic up event, with no BLE or desktop interaction.
./ringphone simulate --event up --backend mirroring --dry-run

# One physical test. Explicitly focus the already-open Mirroring app once.
./ringphone simulate --event up --backend mirroring --enable-output --focus-mirror
```

`simulate` writes a local `state/simulation.json`, not the ring's binding or model
state. It emits at most one accepted command. `output_submitted` means the local
burst completed; **observe the creator/caption changing on the phone** before
recording a successful next-video action. Do not count a new frame in the same
playing video as a successful switch.

## Ring input

```bash
./ringphone run --backend mirroring --dry-run
./ringphone run --backend mirroring --enable-output

# Optional local model, once independently validated on the actual ring.
./ringphone run --source rnn --model your-ring-model --preset rnn \
  --backend mirroring --dry-run
```

The button preset maps a double press to `next`. The RNN preset maps `up` to
`next` and `down` to `previous`. Mirroring uses scroll directions rather than
Switch Control keys; the previous action still needs its own physical test.
The public-data experimental model is not a substitute for a validated ring model.

`run` never activates Mirroring automatically. Foreground, window geometry,
connection-page AX structure, and pointer location are checked before output and
during each burst. Moving the cursor away from the content anchor aborts output.
Native connection, lock/in-use and permission pages are rejected, as are unknown
native phone-control structures on unsupported OS versions. Restore the target
manually and restart after a fatal target error.

Pause, BLE disconnection, or session-generation changes revoke an ongoing burst.
A pause followed immediately by resume does not resurrect the older action.
An input that expires before the first wheel event is discarded. Concurrent
output requests are rejected rather than queued. Partial delivery remains an
unknown phone outcome and is never automatically retried.

## Local calibration

Pass a JSON configuration with these fields, then rerun a single simulation:

```json
{
  "output_backend": "mirroring",
  "mapping": {"up": "next", "down": "previous"},
  "mirror_scroll_pixels": 600,
  "mirror_scroll_duration_s": 0.35,
  "mirror_invert_scroll": false
}
```

Distance is bounded to 120–1200 pixels and duration to 0.1–1 second. Commands are
emitted as 12 wheel events with the exact configured total distance. There
are no mouse clicks, key presses, synthetic scroll phases, momentum events,
automatic retries, or automatic direction changes. If partially delivered input
raises an error, inspect the phone before issuing another command.

## Validation boundary

Most tests use a fake native API. A macOS-only test additionally creates and
inspects real CoreGraphics event objects through the production binding, with
event posting replaced by an inspection callback. Tests cover exact distance and
direction, native ABI preservation, pixel units, HID routing, target/cursor
guards, connection-page rejection, permissions, cancellation, revoked input,
non-queued concurrency, dry-run isolation and BLE-to-output integration.
Physical Douyin pagination remains a separate acceptance check. Status keeps
`posted_swipes` separate from `posted_keys`, and always reports phone delivery
as unverified. `partial_swipes` and `last_output_attempt` record interrupted
delivery, including attempted/submitted event counts. Native prompt rejection
does not prove the actual iPhone app is Douyin or acknowledge a completed swipe.
Global HID dispatch also has an unavoidable small check-to-dispatch race: these
guards reduce cross-window input, not provide an atomic process-bound guarantee.

### Limited physical acceptance

On 2026-10-08, the production simulator was tested against an actual iPhone 14
running Douyin through iPhone Mirroring. One synthetic `up` action changed the
feed item; one synthetic `down` action returned to the original item's identity.
The default 600-pixel / 0.35-second configuration was used. The connected-window
AX checks and native output both passed on that session. No physical ring input
or recognizer was used. This verifies one forward/backward pair, not recognition
accuracy or continuous-session reliability. Detailed observations remain in
local-only state files; no phone screenshots or private state are published.
