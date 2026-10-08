"""Discrete firmware events; no inference confidence is invented."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math

from .config import Config


@dataclass(frozen=True)
class RingEvent:
    name: str
    timestamp_ms: int
    received_at: float  # Host monotonic time, NEVER compared to device uptime.


@dataclass(frozen=True)
class Decision:
    reason: str
    action: str | None = None
    key: str | None = None


def parse_event(sdk, packet, now: float) -> RingEvent | None:
    parsers = {
        0x0701: ("double_tap", sdk.parse_sensor_double_tap_event),
        0x0703: ("key_double_press", sdk.parse_sensor_key_double_press_event),
        0x0704: ("key_single_press", sdk.parse_sensor_key_single_press_event),
    }
    if packet.command == 0x0702:
        event = sdk.parse_sensor_gesture_event(packet.body)
        name = sdk.sensor_gesture_name(event.gesture_id)
    elif packet.command in parsers:
        name, parser = parsers[packet.command]
        event = parser(packet.body)
    else:
        return None
    return RingEvent(name, event.timestamp_ms, now)


class EventGate:
    def __init__(self, config: Config):
        self.config = config
        self.paused = False
        self.reset(0.0)

    def reset(self, now: float) -> None:
        """New verified BLE session; events from the old session are discarded."""
        self.ready_at = now + self.config.settle_s
        self.last_emit_at = -math.inf
        self.seen: set[tuple[str, int]] = set()
        self.order: deque[tuple[str, int]] = deque()
        # reset deliberately preserves pause state across a reconnect.

    def pause(self) -> None:
        self.paused = True

    def resume(self, now: float) -> None:
        self.paused = False
        self.ready_at = now + self.config.settle_s

    def handle(self, event: RingEvent, now: float) -> Decision:
        identity = (event.name, event.timestamp_ms)
        if identity in self.seen:
            return Decision("duplicate")
        self.seen.add(identity)
        self.order.append(identity)
        if len(self.order) > 512:
            self.seen.remove(self.order.popleft())
        age = now - event.received_at
        if not math.isfinite(age) or age < 0 or age > self.config.max_event_age_s:
            return Decision("stale")
        if self.paused:
            return Decision("paused")
        if now < self.ready_at or event.received_at < self.ready_at:
            return Decision("settling")
        action = self.config.mapping.get(event.name)
        if action is None:
            return Decision("unmapped")
        key = self.config.keys[action]
        if key is None:
            return Decision("action-disabled", action)
        if now - self.last_emit_at < self.config.cooldown_s:
            return Decision("cooldown", action)
        self.last_emit_at = now
        return Decision("accepted", action, key)
