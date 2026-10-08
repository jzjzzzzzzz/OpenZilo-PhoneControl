"""Bounded BLE transport. Uses vendor framing/parsers, never stores recordings."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
import time

from .config import Config
from .events import parse_event
from .sdk import load_sdk

CONNECTION_STABILIZATION_S = 3.0


class LinkLost(ConnectionError):
    pass


class IdentityMismatch(ValueError):
    pass


@dataclass(frozen=True)
class Candidate:
    device: object
    address: str
    name: str
    rssi: int


async def scan(timeout: float, scanner_class=None) -> list[Candidate]:
    if scanner_class is None:
        from bleak import BleakScanner
        scanner_class = BleakScanner
    sdk = load_sdk()
    found = await scanner_class.discover(timeout=timeout, return_adv=True)
    result = []
    for device, advert in found.values():
        uuids = {str(u).lower() for u in advert.service_uuids}
        if sdk.NUS_SERVICE_UUID.lower() in uuids:
            result.append(Candidate(device, device.address,
                                    advert.local_name or device.name or "ring", advert.rssi))
    return sorted(result, key=lambda item: item.rssi, reverse=True)


def verify_identity(info, expected: str | None = None) -> None:
    if not str(info.cpuid).strip() or str(info.model).casefold() != "ring_sound":
        raise IdentityMismatch("设备未返回有效 ring_sound 型号及 CPUID")
    if expected and str(info.cpuid).strip().casefold() != expected.strip().casefold():
        raise IdentityMismatch("物理 CPUID 不匹配，已拒绝该设备")


class RingLink:
    def __init__(self, candidate: Candidate, config: Config, *, client_factory=None,
                 clock=time.monotonic, logger=None):
        self.candidate = candidate
        self.config = config
        self.sdk = load_sdk()
        self.clock = clock
        self.log = logger or logging.getLogger("ringphone")
        if client_factory is None:
            from bleak import BleakClient
            client_factory = BleakClient
        self.client = client_factory(candidate.device, disconnected_callback=self._disconnected)
        self.stream = self.sdk.PacketStream()
        self.events: asyncio.Queue = asyncio.Queue(maxsize=32)
        self.imu: asyncio.Queue = asyncio.Queue(maxsize=8)
        self.imu_enabled = False
        self.pending: dict[int, asyncio.Future] = {}
        self.request_lock = asyncio.Lock()
        self.disconnected = asyncio.Event()
        self.accept_events = False
        self.stats = {"packets": 0, "invalid": 0, "ignored": 0, "overflow": 0}

    def clear_events(self) -> None:
        while not self.events.empty():
            self.events.get_nowait()
        while not self.imu.empty():
            self.imu.get_nowait()

    def _disconnected(self, _client) -> None:
        self.accept_events = False
        self.clear_events()
        self.disconnected.set()
        for future in list(self.pending.values()):
            if not future.done():
                future.set_exception(LinkLost("戒指 BLE 已断开"))

    def _notify(self, _sender, data: bytearray) -> None:
        try:
            packets = self.stream.feed(data)
        except self.sdk.ProtocolError as exc:
            self.stream.clear()
            self.stats["invalid"] += 1
            if self.stats["invalid"] <= 3 or self.stats["invalid"] % 100 == 0:
                self.log.warning("丢弃损坏协议包：%s", exc)
            return
        for packet in packets:
            self.stats["packets"] += 1
            if packet.version != 4:
                self.stats["invalid"] += 1
                continue
            future = self.pending.get(packet.command)
            if future is not None and not future.done():
                future.set_result(packet)
                continue
            if not self.accept_events:
                self.stats["ignored"] += 1
                continue
            if packet.command == 0x0605 and self.imu_enabled:
                try:
                    batch = self.sdk.parse_sensor_data_batch(packet.body)
                    if not 1 <= batch.frame_count <= 32:
                        raise self.sdk.ProtocolError("无效 IMU 批量大小")
                    if self.imu.full():
                        self.stats["overflow"] += 1
                        while not self.imu.empty():
                            self.imu.get_nowait()
                        continue
                    self.imu.put_nowait((batch, self.clock()))
                except self.sdk.ProtocolError:
                    self.stats["invalid"] += 1
                continue
            try:
                event = parse_event(self.sdk, packet, self.clock())
            except self.sdk.ProtocolError:
                self.stats["invalid"] += 1
                continue
            if event is None:
                # Includes audio, unrequested IMU and time-sync requests.
                # This bridge never downloads recordings or changes the clock.
                self.stats["ignored"] += 1
                continue
            if self.imu_enabled:
                self.stats["ignored"] += 1
                continue  # RNN and discrete events are mutually exclusive inputs.
            if self.events.full():
                self.stats["overflow"] += 1
                self.clear_events()  # Never accumulate a backlog of phone actions.
                continue
            self.events.put_nowait(event)

    async def _write(self, packet: bytes) -> None:
        for offset in range(0, len(packet), 20):
            await self.client.write_gatt_char(self.sdk.NUS_RX_UUID, packet[offset:offset + 20], response=False)
            await asyncio.sleep(0)

    async def request(self, command, response_command, body=b"", *, timeout_s=None):
        if (int(command), int(response_command)) not in {(0x0101, 0x0102), (0x0601, 0x0602), (0x0603, 0x0604)} or body:
            raise ValueError("该适配器只支持系统信息和 IMU 上报控制")
        async with self.request_lock:
            if self.disconnected.is_set() or not self.client.is_connected:
                raise LinkLost("戒指未连接")
            future = asyncio.get_running_loop().create_future()
            self.pending[int(response_command)] = future
            try:
                async with asyncio.timeout(timeout_s or self.config.command_timeout_s):
                    await self._write(self.sdk.encode_packet(command))
                    packet = await future
                return packet
            finally:
                self.pending.pop(int(response_command), None)
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # Consume an error even when a write failed first.

    async def request_info(self):
        return self.sdk.parse_system_info((await self.request(0x0101, 0x0102)).body)

    async def start_imu(self):
        # SDK's public high-level API uses this adapter's bounded request method.
        info = await self.sdk.start_sensor_report(self)
        self.imu_enabled = True
        return info

    async def connect(self, expected_cpuid: str | None = None):
        try:
            await asyncio.wait_for(self.client.connect(), self.config.connect_timeout_s)
            await asyncio.wait_for(
                self.client.start_notify(self.sdk.NUS_TX_UUID, self._notify),
                self.config.command_timeout_s,
            )
            # Conservative stabilization period; keep notifications active, discard events.
            await asyncio.sleep(CONNECTION_STABILIZATION_S)
            info = await self.request_info()
            verify_identity(info, expected_cpuid)
            return info
        except BaseException:
            await self.close()
            raise

    def activate(self) -> None:
        self.clear_events()
        self.accept_events = True

    async def close(self) -> None:
        if self.imu_enabled and self.client.is_connected and not self.disconnected.is_set():
            try:
                await self.sdk.stop_sensor_report(self, timeout_s=2.0)
            except Exception:
                pass  # BLE disconnect also disables streaming in firmware.
        self.imu_enabled = False
        self._disconnected(self.client)
        # Disconnect itself tears down notifications; don't wait separately for stop_notify.
        try:
            await asyncio.wait_for(self.client.disconnect(), 5.0)
        except Exception as exc:
            self.log.debug("断连清理：%s", exc)


async def connect_profile(profile: dict, config: Config, logger, *, scan_fn=scan,
                          link_factory=RingLink):
    candidates = await scan_fn(config.scan_timeout_s)
    candidates.sort(key=lambda c: c.address.casefold() == profile["address"].casefold(), reverse=True)
    for candidate in candidates:
        link = link_factory(candidate, config, logger=logger)
        try:
            info = await link.connect(profile["cpuid"])
        except Exception as exc:
            logger.info("候选设备连接未通过：%s", exc)
            continue  # RingLink.connect closes on failure, including identity mismatch.
        return link, info
    raise LinkLost("未找到已绑定戒指；请确认已开机、靠近 Mac，且旧推理程序/手机 Demo 已断开")
