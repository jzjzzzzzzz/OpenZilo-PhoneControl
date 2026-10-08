import asyncio
import logging
import struct
from types import SimpleNamespace

from ring_iphone.ble import Candidate
from ring_iphone.sdk import load_sdk


def info_body(cpuid="TEST-CPU", model="ring_sound"):
    def string(value):
        raw = value.encode()
        return struct.pack(">H", len(raw)) + raw
    return (b"\0\0" + string("TEST-FW") + struct.pack(">IIIHB", 0, 1000, 900, 80, 0)
            + string("TEST-SN") + string(cpuid) + string(model))


def candidate(address="TEST-UUID"):
    return Candidate(SimpleNamespace(address=address, name="ring"), address, "ring", -40)


def quiet_logger():
    logger = logging.getLogger("ringphone-tests")
    logger.handlers[:] = [logging.NullHandler()]
    logger.propagate = False
    return logger


class FakeBleak:
    """Only tests instantiate this; production always uses bleak.BleakClient."""
    def __init__(self, device, disconnected_callback):
        self.device = device
        self.callback = disconnected_callback
        self.is_connected = False
        self.notify = None
        self.writes = []
        self.commands = []
        self.closed = False
        self.reply = True
        self.cpuid = "TEST-CPU"
        self.incoming = load_sdk().PacketStream()

    async def connect(self):
        self.is_connected = True

    async def start_notify(self, uuid, callback):
        self.notify = callback

    async def disconnect(self):
        self.closed = True
        self.is_connected = False
        self.callback(self)

    def feed(self, data, size=7):
        for i in range(0, len(data), size):
            self.notify(None, bytearray(data[i:i + size]))

    async def write_gatt_char(self, uuid, data, response):
        assert response is False
        self.writes.append(bytes(data))
        for packet in self.incoming.feed(data):
            self.commands.append(packet.command)
            if packet.command == 0x0101 and self.reply:
                raw = load_sdk().encode_packet(0x0102, info_body(self.cpuid))
                asyncio.get_running_loop().call_soon(self.feed, raw)
            elif packet.command in (0x0601, 0x0603) and self.reply:
                body = struct.pack(">HHHH", 0, 100, 16, 2000) if packet.command == 0x0601 else b"\0\0"
                raw = load_sdk().encode_packet(packet.command + 1, body)
                asyncio.get_running_loop().call_soon(self.feed, raw)


def imu_packet(sequence=0, count=10, start_ms=0):
    body = struct.pack(">HIHH", 0, sequence, count, 16)
    for i in range(count):
        body += struct.pack(">Ihhhhhh", (start_ms + 10 * i) & 0xFFFFFFFF, 100, -200, 300, -400, 500, -600)
    return load_sdk().encode_packet(0x0605, body)
