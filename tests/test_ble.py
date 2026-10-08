import asyncio
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ring_iphone.ble import IdentityMismatch, LinkLost, RingLink, connect_profile, scan, verify_identity
from ring_iphone.config import Config
from ring_iphone.events import EventGate
from ring_iphone.output import DryKeyboard
from ring_iphone.sdk import load_sdk
from fakes import FakeBleak, candidate, quiet_logger


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sdk = load_sdk()
        self.link = RingLink(candidate(), Config(), client_factory=FakeBleak,
                             clock=lambda: 3.0, logger=quiet_logger())
        self.link.client.notify = self.link._notify

    def packet(self, command=0x0703, stamp=1000, suffix=b""):
        return self.sdk.encode_packet(command, struct.pack(">I", stamp) + suffix)

    def test_events_ignored_until_identity_verified(self):
        self.link.client.feed(self.packet())
        self.assertTrue(self.link.events.empty())

    def test_fragmented_and_coalesced_packets(self):
        self.link.activate()
        raw = self.packet() + self.packet(0x0702, 1001, b"\x02")
        self.link.client.feed(raw, size=3)
        self.assertEqual(self.link.events.get_nowait().name, "key_double_press")
        self.assertEqual(self.link.events.get_nowait().name, "rotate_front")

    def test_all_event_types(self):
        self.link.activate()
        for command, extra, name in [(0x0701, b"", "double_tap"),
                                     (0x0702, b"\x01", "rotate_back"),
                                     (0x0702, b"\x03", "wave"),
                                     (0x0704, b"", "key_single_press")]:
            self.link.client.feed(self.packet(command, suffix=extra))
            self.assertEqual(self.link.events.get_nowait().name, name)

    def test_bad_crc_then_next_valid_packet(self):
        self.link.activate()
        raw = bytearray(self.packet())
        raw[-1] ^= 1
        self.link.client.feed(raw)
        self.assertEqual(self.link.stats["invalid"], 1)
        self.assertTrue(self.link.events.empty())
        self.link.client.feed(self.packet())
        self.assertEqual(self.link.events.qsize(), 1)

    def test_strict_event_length_and_v4_only(self):
        self.link.activate()
        self.link.client.feed(self.packet(suffix=b"\0"))
        raw = bytearray(self.packet())
        raw[2] = 3
        self.link.client.feed(raw)
        self.assertEqual(self.link.stats["invalid"], 2)
        self.assertTrue(self.link.events.empty())

    def test_oversized_frame_is_rejected(self):
        self.link.activate()
        raw = struct.pack(">BHHIH", 0x3F, 4, 0x0703, 100000, 0)
        self.link.client.feed(raw)
        self.assertEqual(self.link.stats["invalid"], 1)
        self.assertEqual(len(self.link.stream), 0)

    def test_audio_stream_does_not_create_an_unbounded_queue(self):
        self.link.activate()
        raw = self.sdk.encode_packet(0x0505, b"audio-data" * 100)
        for _ in range(500):
            self.link.client.feed(raw, size=128)
        self.assertEqual(self.link.stats["ignored"], 500)
        self.assertTrue(self.link.events.empty())
        self.assertEqual(len(self.link.stream), 0)

    def test_overflow_clears_backlog(self):
        self.link.activate()
        for stamp in range(33):
            self.link.client.feed(self.packet(stamp=stamp))
        self.assertEqual(self.link.stats["overflow"], 1)
        self.assertTrue(self.link.events.empty())

    async def test_connection_info_and_chunked_writes(self):
        with patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0):
            info = await self.link.connect("TEST-CPU")
        self.assertEqual(info.battery_percent, 80)
        self.assertFalse(self.link.accept_events)
        await self.link._write(self.sdk.encode_packet(0xFFFF, b"x" * 55))
        self.assertTrue(all(len(chunk) <= 20 for chunk in self.link.client.writes))
        await self.link.close()
        self.assertTrue(self.link.client.closed)

    async def test_wrong_identity_closes_before_activating_events(self):
        with patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0):
            with self.assertRaises(IdentityMismatch):
                await self.link.connect("OTHER")
        self.assertTrue(self.link.client.closed)
        self.assertFalse(self.link.accept_events)

    async def test_request_timeout_has_no_leftover_waiters(self):
        self.link.config.command_timeout_s = 0.01
        self.link.client.is_connected = True
        self.link.client.reply = False
        with self.assertRaises(TimeoutError):
            await self.link.request_info()
        self.assertEqual(self.link.pending, {})

    async def test_disconnect_unblocks_request_and_clears_events(self):
        self.link.client.is_connected = True
        self.link.client.reply = False
        self.link.activate()
        self.link.client.feed(self.packet())
        task = asyncio.create_task(self.link.request_info())
        await asyncio.sleep(0)
        await self.link.client.disconnect()
        with self.assertRaises(LinkLost):
            await task
        self.assertTrue(self.link.events.empty())
        self.assertEqual(self.link.pending, {})

    async def test_cancel_during_connect_cleans_up(self):
        started = asyncio.Event()
        async def never_connect():
            self.link.client.is_connected = True
            started.set()
            await asyncio.Future()
        self.link.client.connect = never_connect
        task = asyncio.create_task(self.link.connect())
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(self.link.client.closed)

    async def test_packet_to_output_integration(self):
        with patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0):
            await self.link.connect("TEST-CPU")
        self.link.activate()
        output = DryKeyboard()
        gate = EventGate(Config())
        self.link.client.feed(self.packet() + self.packet(), size=20)
        while not self.link.events.empty():
            decision = gate.handle(self.link.events.get_nowait(), 3.0)
            if decision.key:
                await output.press(decision.key)
        self.assertEqual(output.count, 1)
        await self.link.close()


class DiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_nus_filter_and_rssi_sort(self):
        sdk = load_sdk()
        def row(address, rssi, service):
            return (SimpleNamespace(address=address, name="ring"),
                    SimpleNamespace(local_name=None, rssi=rssi, service_uuids=[service]))
        scanner = SimpleNamespace(discover=AsyncMock(return_value={
            "a": row("a", -80, sdk.NUS_SERVICE_UUID),
            "b": row("b", -30, sdk.NUS_SERVICE_UUID.lower()),
            "c": row("c", -10, "1234"),
        }))
        self.assertEqual([c.address for c in await scan(1, scanner)], ["b", "a"])

    async def test_rotated_uuid_fallback_is_cpuid_verified(self):
        attempts = []
        class Link:
            def __init__(self, c, config, logger):
                self.candidate = c
            async def connect(self, expected):
                attempts.append((self.candidate.address, expected))
                if self.candidate.address == "OLD":
                    raise IdentityMismatch("wrong ring")
                return SimpleNamespace(cpuid=expected)
        scanner = AsyncMock(return_value=[candidate("NEW"), candidate("OLD")])
        link, info = await connect_profile({"address": "OLD", "cpuid": "CPU"}, Config(),
                                          quiet_logger(), scan_fn=scanner, link_factory=Link)
        self.assertEqual(attempts, [("OLD", "CPU"), ("NEW", "CPU")])
        self.assertEqual(link.candidate.address, "NEW")

    def test_identity_requires_model_and_nonempty_cpuid(self):
        for cpu, model in [("", "ring_sound"), ("CPU", "unrelated")]:
            with self.assertRaises(IdentityMismatch):
                verify_identity(SimpleNamespace(cpuid=cpu, model=model))
