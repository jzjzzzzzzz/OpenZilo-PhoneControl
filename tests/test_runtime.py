import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest
from unittest.mock import patch

from ring_iphone.ble import RingLink
from ring_iphone.bridge import run_bridge
from ring_iphone.cli import build_parser, replay
from ring_iphone.config import Config, ROOT
from ring_iphone.events import RingEvent
from ring_iphone.output import DryKeyboard, MacKeyboard
from ring_iphone.sdk import load_sdk
from ring_iphone.storage import write_json
from fakes import FakeBleak, candidate, quiet_logger


class OutputTests(unittest.IsolatedAsyncioTestCase):
    def make_keyboard(self, trusted=True, up=2):
        keyboard = MacKeyboard.__new__(MacKeyboard)
        keyboard.hold_s = 0.1
        self.posted = []
        self.released = []
        self.flags = []
        keyboard.api = SimpleNamespace(
            AXIsProcessTrusted=lambda: trusted,
            CGEventCreateKeyboardEvent=lambda source, code, down: 1 if down else up,
            CGEventSetFlags=lambda event, flags: self.flags.append((event, flags)),
            CGEventPost=lambda tap, event: self.posted.append(event),
        )
        keyboard.cf = SimpleNamespace(CFRelease=self.released.append)
        return keyboard

    async def test_cancel_during_keydown_still_sends_keyup(self):
        keyboard = self.make_keyboard()
        task = asyncio.create_task(keyboard.press("SPACE"))
        await asyncio.sleep(0)
        self.assertEqual(self.posted, [1])
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.posted, [1, 2])
        self.assertEqual(self.released, [1, 2])
        self.assertEqual(self.flags, [(1, 0), (2, 0)])

    async def test_permission_denied_never_posts(self):
        keyboard = self.make_keyboard(trusted=False)
        with self.assertRaises(PermissionError):
            await keyboard.press("SPACE")
        self.assertEqual(self.posted, [])

    async def test_up_allocation_failure_never_posts_down(self):
        keyboard = self.make_keyboard(up=0)
        with self.assertRaises(RuntimeError):
            await keyboard.press("SPACE")
        self.assertEqual(self.posted, [])
        self.assertEqual(self.released, [1])


class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        write_json(self.state / "ring-profile.json", {
            "schema_version": 1, "address": "TEST-UUID", "cpuid": "TEST-CPU",
        })
        self.config = Config(settle_s=0)
        self.links = []
        self.patch = patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0)
        self.patch.start()

    async def asyncTearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    async def connect(self, profile, config, logger):
        link = RingLink(candidate(), config, client_factory=FakeBleak, logger=logger)
        info = await link.connect(profile["cpuid"])
        self.links.append(link)
        def send():
            if not link.disconnected.is_set():
                link.events.put_nowait(RingEvent("key_double_press", 1234, time.monotonic()))
        asyncio.get_running_loop().call_later(0.01, send)
        return link, info

    async def test_dry_session_counts_and_stops_cleanly(self):
        status = await run_bridge(self.config, self.state, quiet_logger(), duration=0.07,
                                  connector=self.connect)
        self.assertEqual(status["simulated_keys"], 1)
        self.assertEqual(status["posted_keys"], 0)
        self.assertEqual(status["state"], "stopped")
        self.assertEqual(status["iphone_delivery"], "unverified")
        self.assertTrue(self.links[0].client.closed)
        self.assertEqual(json.loads((self.state / "status.json").read_text())["state"], "stopped")

    async def test_monitor_never_calls_output(self):
        output = DryKeyboard()
        status = await run_bridge(self.config, self.state, quiet_logger(), duration=0.07,
                                  connector=self.connect, keyboard=output, monitor=True)
        self.assertEqual(output.count, 0)
        self.assertEqual(status["mode"], "monitor")
        self.assertEqual(status["last_event"]["name"], "key_double_press")

    async def test_mirroring_dry_bridge_counts_swipes_not_keys(self):
        self.config.output_backend = "mirroring"
        status = await run_bridge(self.config, self.state, quiet_logger(), duration=0.07,
                                  connector=self.connect)
        self.assertEqual(status["simulated_swipes"], 1)
        self.assertEqual(status["simulated_keys"], 0)
        self.assertEqual(status["posted_swipes"], 0)
        self.assertEqual(status["iphone_delivery"], "unverified")

    async def test_pausing_during_swipe_keeps_bridge_paused_and_records_partial(self):
        from ring_iphone.events import EventGate
        from ring_iphone.mirroring import MirrorOutputInterrupted
        self.config.output_backend = "mirroring"
        gate = EventGate(self.config)
        class PausingOutput:
            trusted = True
            last_attempt = None
            async def perform(output, action, *, guard, valid_until):
                self.assertTrue(guard())
                self.assertGreater(valid_until, time.monotonic())
                gate.pause()
                self.assertFalse(guard())
                output.last_attempt = {"action": action, "attempted_scroll_events": 1,
                                       "submitted_scroll_events": 1, "completed": False, "outcome": "unknown"}
                raise MirrorOutputInterrupted("paused")
        with patch("ring_iphone.bridge.EventGate", return_value=gate):
            status = await run_bridge(self.config, self.state, quiet_logger(), duration=0.07,
                                      connector=self.connect, keyboard=PausingOutput(), enable_output=True)
        self.assertEqual(status["partial_swipes"], 1)
        self.assertEqual(status["posted_swipes"], 0)
        self.assertEqual(status["last_output_attempt"]["outcome"], "unknown")
        self.assertEqual(len(self.links), 1)

    async def test_fatal_partial_swipe_is_recorded_and_never_retried(self):
        self.config.output_backend = "mirroring"
        class BrokenMirror:
            trusted = True
            last_attempt = None
            async def perform(output, action, **kwargs):
                output.last_attempt = {"action": action, "attempted_scroll_events": 1,
                                       "submitted_scroll_events": 1, "completed": False, "outcome": "unknown"}
                raise RuntimeError("foreground changed")
        with self.assertRaises(RuntimeError):
            await run_bridge(self.config, self.state, quiet_logger(), duration=0.2,
                             connector=self.connect, keyboard=BrokenMirror(), enable_output=True)
        status = json.loads((self.state / "status.json").read_text())
        self.assertEqual(status["partial_swipes"], 1)
        self.assertEqual(status["posted_swipes"], 0)
        self.assertEqual(len(self.links), 1)

    async def test_output_failure_is_fatal_not_replayed(self):
        class BrokenOutput:
            trusted = True
            async def press(self, key):
                raise PermissionError("revoked")
        with self.assertRaises(PermissionError):
            await run_bridge(self.config, self.state, quiet_logger(), duration=0.2,
                             connector=self.connect, keyboard=BrokenOutput(), enable_output=True)
        self.assertEqual(len(self.links), 1)
        self.assertTrue(self.links[0].client.closed)
        status = json.loads((self.state / "status.json").read_text())
        self.assertEqual(status["posted_keys"], 0)
        self.assertIn("revoked", status["error"])

    async def test_disconnect_reconnect_resets_timestamp_dedup(self):
        async def connector(*args):
            link, info = await self.connect(*args)
            if len(self.links) == 1:
                asyncio.get_running_loop().call_later(
                    0.03, lambda: link._disconnected(link.client))
            return link, info
        status = await run_bridge(self.config, self.state, quiet_logger(), duration=1.15,
                                  connector=connector)
        self.assertEqual(status["session"], 2)
        self.assertEqual(status["simulated_keys"], 2)
        self.assertTrue(all(link.client.closed for link in self.links))

    async def test_cancellation_during_scan_updates_status(self):
        entered = asyncio.Event()
        async def never_connect(*args):
            entered.set()
            await asyncio.Future()
        task = asyncio.create_task(run_bridge(self.config, self.state, quiet_logger(),
                                              connector=never_connect))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        status = json.loads((self.state / "status.json").read_text())
        self.assertFalse(status["connected"])
        self.assertEqual(status["state"], "stopped")


class CLITests(unittest.TestCase):
    def test_output_is_opt_in_and_duration_bounded(self):
        parser = build_parser()
        args = parser.parse_args(["run"])
        self.assertFalse(args.enable_output)
        self.assertIsNone(args.duration)
        args = parser.parse_args(["run", "--enable-output", "--duration", "3"])
        self.assertTrue(args.enable_output)
        self.assertEqual(args.duration, 3)

    def test_offline_example_has_exactly_two_simulated_outputs(self):
        capture = io.StringIO()
        with redirect_stdout(capture):
            replay(ROOT / "examples/events.jsonl", Config())
        self.assertIn("2 次模拟输出", capture.getvalue())
        self.assertIn('"reason": "stale"', capture.getvalue())
        self.assertIn('"reason": "duplicate"', capture.getvalue())

    def test_replay_rejects_time_reversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"at_s":2,"timestamp_ms":1,"event":"wave"}\n'
                            '{"at_s":1,"timestamp_ms":2,"event":"wave"}\n')
            with redirect_stdout(io.StringIO()), self.assertRaises(ValueError):
                replay(path, Config())
