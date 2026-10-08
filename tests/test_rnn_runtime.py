import asyncio
import json
from pathlib import Path
import struct
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from ring_iphone.ble import RingLink
from ring_iphone.bridge import run_bridge
from ring_iphone.config import Config
from ring_iphone.output import DryKeyboard
from ring_iphone.storage import write_json
from fakes import FakeBleak, candidate, imu_packet, quiet_logger


class IMUTransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.link = RingLink(candidate(), Config(), client_factory=FakeBleak, logger=quiet_logger())
        with patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0):
            await self.link.connect("TEST-CPU")
        self.link.activate()

    async def asyncTearDown(self):
        await self.link.close()

    async def test_imu_is_opt_in_and_stop_is_sent(self):
        self.link.client.feed(imu_packet())
        self.assertTrue(self.link.imu.empty())
        info = await self.link.start_imu()
        self.assertEqual(info.sample_rate_hz, 100)
        self.link.client.feed(imu_packet())
        self.assertEqual(self.link.imu.qsize(), 1)
        await self.link.close()
        self.assertEqual(self.link.client.commands, [0x0101, 0x0601, 0x0603])
        self.assertTrue(self.link.imu.empty())

    async def test_rnn_does_not_queue_firmware_events(self):
        await self.link.start_imu()
        self.link.client.feed(imu_packet())
        for stamp in range(40):
            self.link.client.feed(self.link.sdk.encode_packet(0x0703, struct.pack(">HI", 0, stamp)))
        self.assertTrue(self.link.events.empty())
        self.assertEqual(self.link.imu.qsize(), 1)

    async def test_overflow_drops_backlog(self):
        await self.link.start_imu()
        for i in range(9):
            self.link.client.feed(imu_packet(sequence=i*10, start_ms=i*100))
        self.assertTrue(self.link.imu.empty())
        self.assertEqual(self.link.stats["overflow"], 1)

    async def test_invalid_batch_count_rejected(self):
        await self.link.start_imu()
        self.link.client.feed(imu_packet(count=33))
        self.link.client.feed(imu_packet(count=0))
        self.assertTrue(self.link.imu.empty())
        self.assertEqual(self.link.stats["invalid"], 2)

    async def test_write_command_allowlist(self):
        for command, response, body in ((0x0301, 0x0302, b""), (0x0101, 0x0102, b"x")):
            with self.assertRaises(ValueError):
                await self.link.request(command, response, body)
        self.assertEqual(self.link.client.commands, [0x0101])


class FakeArtifact:
    def __init__(self, *, slow=False, mismatch=False):
        self.model = SimpleNamespace(window_seconds=0.1, stride_seconds=0.05)
        self.manifest = {"name": "synthetic", "sample_rate_hz": 100}
        self.calls = 0
        self.slow = slow
        self.mismatch = mismatch

    def check_sensor(self, info):
        if self.mismatch:
            raise ValueError("sensor mismatch")
        assert info.sample_rate_hz == 100

    def predict(self, window):
        self.calls += 1
        if self.slow:
            time.sleep(0.1)
        return {"idle": 0.99, "up": 0.01} if self.calls == 1 else {"idle": 0.01, "up": 0.99}


class RNNRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name)
        write_json(self.state / "ring-profile.json", {
            "schema_version": 1, "address": "TEST-UUID", "cpuid": "TEST-CPU"})
        self.config = Config(mapping={"up": "next"}, settle_s=0)
        self.links = []
        self.timers = []
        self.patch = patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0)
        self.patch.start()

    async def asyncTearDown(self):
        for timer in self.timers:
            timer.cancel()
        self.patch.stop()
        self.tmp.cleanup()

    async def connector(self, profile, config, logger):
        link = RingLink(candidate(), config, client_factory=FakeBleak, logger=logger)
        info = await link.connect(profile["cpuid"])
        self.links.append(link)
        for i in range(4):
            def send(index=i):
                if not link.disconnected.is_set():
                    link.client.feed(imu_packet(sequence=index*10, start_ms=index*100))
            self.timers.append(asyncio.get_running_loop().call_later(0.04*(i+1), send))
        return link, info

    async def test_rnn_to_dry_keyboard_and_clean_stop(self):
        artifact = FakeArtifact()
        status = await run_bridge(self.config, self.state, quiet_logger(), artifact=artifact,
                                  connector=self.connector, duration=0.23)
        self.assertEqual(status["simulated_keys"], 1)
        self.assertEqual(status["last_event"]["name"], "up")
        self.assertEqual(status["source"], "rnn")
        self.assertEqual(status["iphone_delivery"], "unverified")
        self.assertEqual(self.links[0].client.commands[-1], 0x0603)

    async def test_stale_inference_never_outputs(self):
        artifact = FakeArtifact(slow=True)
        self.config.max_event_age_s = 0.05
        status = await run_bridge(self.config, self.state, quiet_logger(), artifact=artifact,
                                  connector=self.connector, duration=0.35)
        self.assertGreater(artifact.calls, 0)
        self.assertEqual(status["simulated_keys"], 0)

    async def test_settling_batches_never_rearm_or_infer(self):
        artifact = FakeArtifact()
        self.config.settle_s = 1
        status = await run_bridge(self.config, self.state, quiet_logger(), artifact=artifact,
                                  connector=self.connector, duration=0.23)
        self.assertEqual(artifact.calls, 0)
        self.assertEqual(status["simulated_keys"], 0)

    async def test_sensor_mismatch_closes_without_inference(self):
        artifact = FakeArtifact(mismatch=True)
        with self.assertRaisesRegex(ValueError, "sensor mismatch"):
            await run_bridge(self.config, self.state, quiet_logger(), artifact=artifact,
                             connector=self.connector, duration=0.2)
        self.assertEqual(artifact.calls, 0)
        self.assertTrue(self.links[0].client.closed)
        self.assertEqual(self.links[0].client.commands[-1], 0x0603)

    async def test_rnn_monitor_never_outputs(self):
        output = DryKeyboard()
        status = await run_bridge(self.config, self.state, quiet_logger(), artifact=FakeArtifact(),
                                  connector=self.connector, duration=0.23, monitor=True, keyboard=output)
        self.assertEqual(output.count, 0)
        self.assertEqual(status["last_event"]["name"], "up")
