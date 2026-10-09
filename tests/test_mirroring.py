import asyncio
import ctypes as C
from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import sys
import json
from unittest.mock import patch

from ring_iphone.cli import build_parser, simulate_event, main
from ring_iphone.config import Config, load_config
from ring_iphone.events import EventGate, RingEvent
from ring_iphone.mirroring import (MacMirroring, MirrorTarget, NativeMirrorAPI, scroll_deltas,
                                  bind_scroll_creation, assert_connection_controls, MirrorOutputInterrupted)


class PlanTests(unittest.TestCase):
    def test_native_connection_pages_fail_closed(self):
        for controls in ([], [('AXWindow', ''), ('AXStaticText', '')],
                         [('AXWindow', ''), ('AXButton', '')], [('AXSheet', '')]):
            with self.assertRaises(RuntimeError):
                assert_connection_controls(controls)
        assert_connection_controls([('AXWindow', ''), ('AXGroup', ''), ('AXButton', 'AXCloseButton')])

    def test_ax_tree_walk_releases_owned_arrays_and_application(self):
        api = NativeMirrorAPI.__new__(NativeMirrorAPI)
        released = []
        arrays = {100: [2], 200: [3], 300: []}
        api.ax = SimpleNamespace(AXUIElementCreateApplication=lambda pid: 1)
        api.cf = SimpleNamespace(CFGetTypeID=lambda value: 42, CFArrayGetTypeID=lambda: 42,
                                  CFArrayGetCount=lambda value: len(arrays[value]),
                                  CFArrayGetValueAtIndex=lambda value, index: arrays[value][index],
                                  CFRelease=released.append)
        api._ax_copy = lambda element, name, **kwargs: 100 if name == 'AXWindows' else element * 100
        api._ax_text = lambda element, name: ({2: 'AXWindow', 3: 'AXStaticText'}[element]
                                             if name == 'AXRole' else '')
        controls = api._native_controls(123)
        self.assertEqual(controls, [('AXWindow', ''), ('AXStaticText', '')])
        self.assertEqual(set(released), {1, 100, 200, 300})
        with self.assertRaises(RuntimeError):
            assert_connection_controls(controls)

    def test_ax_read_error_fails_closed_and_releases_root_array(self):
        api = NativeMirrorAPI.__new__(NativeMirrorAPI)
        released = []
        api.ax = SimpleNamespace(AXUIElementCreateApplication=lambda pid: 1)
        api.cf = SimpleNamespace(CFGetTypeID=lambda value: 42, CFArrayGetTypeID=lambda: 42,
                                  CFArrayGetCount=lambda value: 1,
                                  CFArrayGetValueAtIndex=lambda value, index: 2,
                                  CFRelease=released.append)
        api._ax_copy = lambda *args, **kwargs: 100
        def broken(*args):
            raise RuntimeError('cannot read role')
        api._ax_text = broken
        with self.assertRaises(RuntimeError):
            api._native_controls(123)
        self.assertEqual(set(released), {1, 100})

    def test_invalid_target_geometry_fails_closed(self):
        for fields in ((0, 1, 0, 0, 320, 700), (1, 1, float('nan'), 0, 320, 700),
                       (1, 1, 0, 0, 900, 400)):
            with self.assertRaises(ValueError):
                MirrorTarget(*fields)

    def test_cli_preserves_config_backend_and_mapping(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config.json'
            config.write_text(json.dumps({'output_backend': 'switch-control',
                                          'mapping': {'key_double_press': 'next'}}))
            with redirect_stdout(io.StringIO()), patch('ring_iphone.cli.load_sdk') as sdk:
                result = main(['simulate', '--config', str(config), '--event', 'key_double_press',
                               '--state-dir', tmp, '--dry-run'])
            sdk.assert_not_called()
            report = json.loads((Path(tmp) / 'simulation.json').read_text())
            self.assertEqual(result, 0)
            self.assertEqual(report['output_backend'], 'switch-control')
            self.assertEqual(report['decision']['reason'], 'accepted')
    def test_distance_is_exact_and_direction_is_explicit(self):
        for natural in (False, True):
            for invert in (False, True):
                next_deltas = scroll_deltas('next', 601, natural=natural, invert=invert)
                previous = scroll_deltas('previous', 601, natural=natural, invert=invert)
                self.assertEqual(sum(map(abs, next_deltas)), 601)
                self.assertEqual(previous, [-value for value in next_deltas])
                self.assertTrue(all(value != 0 for value in next_deltas))
        self.assertLess(sum(scroll_deltas('next', 600, natural=True, invert=False)), 0)
        self.assertGreater(sum(scroll_deltas('next', 600, natural=False, invert=False)), 0)

    def test_invalid_plan_parameters(self):
        for amount in (True, 119, 1201, 600.0):
            with self.assertRaises(ValueError):
                scroll_deltas('next', amount, natural=True, invert=False)
        with self.assertRaises(ValueError):
            scroll_deltas('like', 600, natural=True, invert=False)
        with self.assertRaises(ValueError):
            scroll_deltas('next', 600, natural=True, invert=False, steps=0)

    def test_backend_configuration(self):
        Config(output_backend='mirroring').validate()
        Config(output_backend='mirroring', keys={'next': None, 'previous': None}).validate()
        for kwargs in ({'output_backend': 'unknown'}, {'mirror_scroll_pixels': True},
                       {'mirror_invert_scroll': 1}, {'mirror_scroll_duration_s': 0}):
            with self.assertRaises(ValueError):
                Config(**kwargs).validate()

    def test_mirroring_actions_do_not_require_switch_keys(self):
        config = load_config(preset='rnn')
        config.output_backend = 'mirroring'
        gate = EventGate(config)
        gate.reset(0)
        decision = gate.handle(RingEvent('down', 2000, 2), 2)
        self.assertEqual((decision.reason, decision.action, decision.key), ('accepted', 'previous', None))
        self.assertEqual(gate.handle(RingEvent('down', 2000, 3), 3).reason, 'duplicate')

    def test_native_pixel_units_hid_route_and_no_touch_phases(self):
        api = NativeMirrorAPI.__new__(NativeMirrorAPI)
        calls, released = [], []
        api.cg = SimpleNamespace(
            CGEventCreateScrollWheelEvent2=lambda source, unit, wheels, delta, x, z:
                calls.append(('create', unit, wheels, delta, x, z)) or 123,
            CGEventSetFlags=lambda event, flags: calls.append(('flags', flags)),
            CGEventPost=lambda tap, event: calls.append(('post', tap)),
        )
        api.cf = SimpleNamespace(CFRelease=released.append)
        api.scroll(-50)
        self.assertEqual(calls, [('create', 0, 1, -50, 0, 0), ('flags', 0), ('post', 0)])
        self.assertEqual(released, [123])

    def test_native_allocation_failure_never_posts(self):
        api = NativeMirrorAPI.__new__(NativeMirrorAPI)
        posted = []
        api.cg = SimpleNamespace(CGEventCreateScrollWheelEvent2=lambda *a: 0,
                                 CGEventPost=lambda *a: posted.append(a))
        with self.assertRaises(RuntimeError):
            api.scroll(50)
        self.assertEqual(posted, [])


@unittest.skipUnless(sys.platform == 'darwin', 'CoreGraphics event-object ABI regression runs on macOS')
class NativeEventObjectTests(unittest.TestCase):
    """Real event allocation/inspection, but NEVER real CGEventPost."""
    def test_production_binding_preserves_signed_pixel_delta(self):
        cg = C.CDLL('/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics')
        cf = C.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
        bind_scroll_creation(cg)
        cg.CGEventGetIntegerValueField.argtypes = [C.c_void_p, C.c_uint32]
        cg.CGEventGetIntegerValueField.restype = C.c_int64
        cg.CGEventSetFlags.argtypes = [C.c_void_p, C.c_uint64]
        cg.CGEventSetFlags.restype = None
        cf.CFRelease.argtypes = [C.c_void_p]
        cf.CFRelease.restype = None
        observed = []
        def inspect_instead_of_post(tap, event):
            observed.append((tap, cg.CGEventGetIntegerValueField(event, 96),
                             cg.CGEventGetIntegerValueField(event, 99)))
        api = NativeMirrorAPI.__new__(NativeMirrorAPI)
        api.cg = SimpleNamespace(CGEventCreateScrollWheelEvent2=cg.CGEventCreateScrollWheelEvent2,
                                 CGEventSetFlags=cg.CGEventSetFlags,
                                 CGEventPost=inspect_instead_of_post)
        api.cf = cf
        for delta in (-50, 50, -1, 1):
            api.scroll(delta)
        self.assertEqual(observed, [(0, delta, 0) for delta in (-50, 50, -1, 1)])


class FakeAPI:
    trusted = True
    natural_scrolling = True
    def __init__(self):
        self.window = MirrorTarget(123, 456, 10, 20, 320, 700)
        self.moves, self.deltas = [], []
        self.targets = 0
        self.change_at = None
        self.point = self.window.anchor

    def target(self):
        self.targets += 1
        if self.change_at == self.targets:
            raise RuntimeError('foreground changed')
        return self.window

    def move(self, point):
        self.moves.append(point)
        self.point = point

    def pointer(self):
        return self.point

    def scroll(self, delta):
        self.deltas.append(delta)


async def yield_only(delay):
    await asyncio.sleep(0)


class OutputTests(unittest.IsolatedAsyncioTestCase):
    def make(self, api=None):
        api = api or FakeAPI()
        return MacMirroring(Config(output_backend='mirroring'), api=api, sleep=yield_only), api

    async def test_bounded_burst_no_key_or_click(self):
        output, api = self.make()
        await output.perform('next')
        self.assertEqual(api.moves, [api.window.anchor])
        self.assertEqual(len(api.deltas), 12)
        self.assertEqual(sum(api.deltas), -600)
        self.assertTrue(output.last_attempt['completed'])
        self.assertEqual(output.last_attempt['submitted_scroll_events'], 12)

    async def test_permission_denied_has_no_ui_effect(self):
        output, api = self.make()
        api.trusted = False
        with self.assertRaises(PermissionError):
            await output.perform('next')
        self.assertEqual((api.targets, api.moves, api.deltas), (0, [], []))

    async def test_foreground_change_stops_without_retry(self):
        output, api = self.make()
        api.change_at = 3
        with self.assertRaises(RuntimeError):
            await output.perform('next')
        self.assertEqual(len(api.deltas), 1)
        self.assertEqual(output.last_attempt['outcome'], 'unknown')

    async def test_window_change_stops_before_first_scroll(self):
        output, api = self.make()
        async def moved_window(delay):
            api.window = MirrorTarget(123, 456, 50, 20, 320, 700)
        output.sleep = moved_window
        with self.assertRaises(RuntimeError):
            await output.perform('next')
        self.assertEqual(api.deltas, [])

    async def test_cancel_does_not_emit_remainder_or_synthetic_end(self):
        output, api = self.make()
        entered = asyncio.Event()
        async def block_after_first(delay):
            if api.deltas:
                entered.set()
                await asyncio.Future()
        output.sleep = block_after_first
        task = asyncio.create_task(output.perform('next'))
        await entered.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(len(api.deltas), 1)

    async def test_concurrent_gestures_do_not_queue_old_commands(self):
        output, api = self.make()
        results = await asyncio.gather(output.perform('next'), output.perform('previous'), return_exceptions=True)
        self.assertIsNone(results[0])
        self.assertIsInstance(results[1], MirrorOutputInterrupted)
        self.assertEqual(len(api.moves), 1)
        self.assertEqual(api.deltas, [-50] * 12)

    async def test_revoked_input_stops_mid_burst(self):
        output, api = self.make()
        with self.assertRaises(MirrorOutputInterrupted):
            await output.perform('next', guard=lambda: len(api.deltas) < 1)
        self.assertEqual(len(api.deltas), 1)
        self.assertEqual(output.last_attempt['outcome'], 'unknown')

    async def test_expired_input_never_moves_pointer(self):
        output, api = self.make()
        output.clock = lambda: 10
        with self.assertRaises(MirrorOutputInterrupted):
            await output.perform('next', valid_until=9)
        self.assertEqual(api.moves, [])
        self.assertEqual(output.last_attempt['outcome'], 'not_sent')

    async def test_expiry_during_pointer_settle_does_not_start_scroll(self):
        output, api = self.make()
        clock = [8]
        output.clock = lambda: clock[0]
        async def expired(delay):
            clock[0] = 10
        output.sleep = expired
        with self.assertRaises(MirrorOutputInterrupted):
            await output.perform('next', valid_until=9)
        self.assertEqual(api.deltas, [])
        self.assertEqual(output.last_attempt['outcome'], 'not_sent')

    async def test_user_pointer_motion_aborts_before_scroll(self):
        output, api = self.make()
        async def moved(delay):
            api.point = (0, 0)
        output.sleep = moved
        with self.assertRaises(RuntimeError):
            await output.perform('next')
        self.assertEqual(api.deltas, [])

    async def test_invalid_deadline_is_rejected_without_native_calls(self):
        for value in (float('nan'), float('inf'), True):
            output, api = self.make()
            with self.assertRaises(ValueError):
                await output.perform('next', valid_until=value)
            self.assertEqual(api.targets, 0)

    async def test_pause_then_resume_still_revokes_old_generation(self):
        config = Config(output_backend='mirroring', settle_s=0)
        gate = EventGate(config)
        generation = gate.generation
        output, api = self.make()
        async def pause_and_resume(delay):
            if api.deltas:
                gate.pause()
                gate.resume(0)
        output.sleep = pause_and_resume
        with self.assertRaises(MirrorOutputInterrupted):
            await output.perform('next', guard=lambda: gate.generation == generation and not gate.paused)
        self.assertEqual(len(api.deltas), 1)

    async def test_native_failure_has_unknown_progress_not_false_zero(self):
        output, api = self.make()
        def broken(delta):
            raise RuntimeError('delivery unavailable')
        api.scroll = broken
        with self.assertRaises(RuntimeError):
            await output.perform('next')
        self.assertEqual(output.last_attempt['attempted_scroll_events'], 1)
        self.assertEqual(output.last_attempt['submitted_scroll_events'], 0)
        self.assertEqual(output.last_attempt['outcome'], 'unknown')

    async def test_ignored_live_input_does_not_construct_or_focus_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(['simulate', '--event', 'left', '--enable-output',
                                             '--focus-mirror', '--state-dir', tmp])
            config = load_config(preset='rnn')
            config.output_backend = 'mirroring'
            with patch('ring_iphone.mirroring.MacMirroring') as output, redirect_stdout(io.StringIO()):
                report = await simulate_event(args, config)
            output.assert_not_called()
            self.assertEqual(report['status'], 'ignored')
            self.assertFalse(report['output_attempted'])

    async def test_dry_simulator_does_not_construct_native_api(self):
        with tempfile.TemporaryDirectory() as tmp:
            args = build_parser().parse_args(['simulate', '--state-dir', tmp, '--dry-run', '--focus-mirror'])
            config = load_config(preset='rnn')
            config.output_backend = 'mirroring'
            with patch('ring_iphone.mirroring.NativeMirrorAPI') as native, redirect_stdout(io.StringIO()):
                report = await simulate_event(args, config)
            native.assert_not_called()
            self.assertEqual(report['decision']['action'], 'next')
            self.assertEqual(report['decision']['reason'], 'accepted')
            self.assertFalse(report['output_attempted'])
            self.assertTrue((Path(tmp) / 'simulation.json').is_file())
