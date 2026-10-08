import json
import math
from pathlib import Path
import tempfile
import unittest

from ring_iphone.config import Config, load_config
from ring_iphone.events import EventGate, RingEvent
from ring_iphone.storage import InstanceLock, read_profile, write_json


class ConfigTests(unittest.TestCase):
    def test_default_is_one_discrete_button(self):
        c = load_config()
        self.assertEqual(c.mapping, {"key_double_press": "next"})
        self.assertIsNone(c.keys["previous"])

    def test_gesture_preset_does_not_enable_second_switch(self):
        c = load_config(preset="gesture")
        self.assertEqual(c.mapping["rotate_back"], "previous")
        self.assertIsNone(c.keys["previous"])

    def test_examples_validate(self):
        for path in (Path(__file__).resolve().parents[1] / "examples").glob("*.json"):
            load_config(path)

    def test_invalid_configuration(self):
        for kwargs in [
            {"mapping": {"key_single_press": "next"}},
            {"mapping": {"double_tap": ["next"]}},
            {"mapping": {}}, {"mapping": []},
            {"keys": {"next": "SPACE", "previous": "SPACE"}},
            {"keys": {"next": "ENTER", "previous": None}},
            {"keys": {"next": None, "previous": None}},
            {"keys": {"next": "SPACE"}},
            {"cooldown_s": math.nan}, {"settle_s": True},
            {"key_hold_s": 5}, {"heartbeat_s": 0},
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Config(**kwargs).validate()

    def test_unknown_fields_and_non_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            for value in [{"mappng": {}}, []]:
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    load_config(path)


class GateTests(unittest.TestCase):
    def setUp(self):
        self.gate = EventGate(Config())
        self.gate.reset(0)

    def event(self, at, stamp=None, name="key_double_press", age=0):
        return self.gate.handle(RingEvent(name, int(at * 1000) if stamp is None else stamp, at - age), at)

    def test_start_settle_and_single_action(self):
        self.assertEqual(self.event(0.1).reason, "settling")
        self.assertEqual(self.event(2).key, "SPACE")

    def test_duplicate_and_cooldown_never_delayed(self):
        self.event(2)
        self.assertEqual(self.event(2.1, stamp=2000).reason, "duplicate")
        self.assertEqual(self.event(2.2).reason, "cooldown")
        self.assertEqual(self.event(4, stamp=2200).reason, "duplicate")
        self.assertEqual(self.event(5).reason, "accepted")

    def test_single_click_and_unknown_never_emit(self):
        for name in ["key_single_press", "idle", "unknown(5)", "double_tap"]:
            self.assertEqual(self.event(2, name=name).reason, "unmapped")

    def test_stale_future_and_nan_are_ignored(self):
        self.assertEqual(self.event(3, age=1).reason, "stale")
        self.assertEqual(self.event(4, age=-1).reason, "stale")
        self.assertEqual(self.event(5, age=math.nan).reason, "stale")

    def test_pause_survives_reconnect_and_resume_drops_old_events(self):
        self.gate.pause()
        self.assertEqual(self.event(2).reason, "paused")
        self.gate.reset(3)
        self.assertEqual(self.event(5).reason, "paused")
        self.gate.resume(6)
        self.assertEqual(self.event(7.6, age=0.3).reason, "settling")
        self.assertEqual(self.event(8).reason, "accepted")

    def test_session_reset_handles_ring_timestamp_reset(self):
        self.assertEqual(self.event(2, stamp=42).reason, "accepted")
        self.gate.reset(5)
        self.assertEqual(self.event(7, stamp=42).reason, "accepted")

    def test_device_uptime_is_not_used_as_host_clock(self):
        self.assertEqual(self.event(2, stamp=0xFFFFFFFF).reason, "accepted")
        self.assertEqual(self.event(3, stamp=1).reason, "accepted")

    def test_previous_disabled_unless_separate_key_configured(self):
        config = load_config(preset="gesture")
        self.gate = EventGate(config)
        self.assertEqual(self.event(2, name="rotate_back").reason, "action-disabled")
        config.keys["previous"] = "F14"
        self.assertEqual(self.event(3, name="rotate_back").key, "F14")

    def test_duplicate_cache_is_bounded(self):
        for stamp in range(2000):
            self.event(3 + stamp, stamp=stamp)
        self.assertEqual(len(self.gate.seen), 512)
        self.assertEqual(len(self.gate.order), 512)


class StorageTests(unittest.TestCase):
    def test_private_atomic_profile_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "private" / "profile.json"
            payload = {"schema_version": 1, "address": "UUID", "cpuid": "CPU"}
            write_json(path, payload)
            self.assertEqual(read_profile(path), payload)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(path.parent.glob(".tmp-*")), [])

    def test_reject_invalid_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "p.json"
            for value in [[], {}, {"schema_version": 1},
                          {"schema_version": 1, "address": "a", "cpuid": ""}]:
                path.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    read_profile(path)

    def test_single_instance_lock_releases(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bridge.lock"
            with InstanceLock(path):
                with self.assertRaises(RuntimeError):
                    with InstanceLock(path):
                        pass
            with InstanceLock(path):
                self.assertTrue(path.exists())
