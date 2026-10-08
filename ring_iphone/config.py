from __future__ import annotations

from dataclasses import dataclass, field, fields
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_CODES = {"SPACE": 49, "F13": 105, "F14": 107, "F15": 113,
             "F16": 106, "F17": 64, "F18": 79, "F19": 80, "F20": 90}
EVENTS = {"key_double_press", "double_tap", "rotate_front", "rotate_back", "wave", "up", "down", "left", "right"}
ACTIONS = {"next", "previous"}


@dataclass
class Config:
    mapping: dict[str, str] = field(default_factory=lambda: {"key_double_press": "next"})
    keys: dict[str, str | None] = field(default_factory=lambda: {"next": "SPACE", "previous": None})
    cooldown_s: float = 0.8
    max_event_age_s: float = 0.5
    settle_s: float = 1.5
    scan_timeout_s: float = 8.0
    connect_timeout_s: float = 20.0
    command_timeout_s: float = 8.0
    heartbeat_s: float = 20.0
    key_hold_s: float = 0.06
    rnn_confidence: float = 0.92
    rnn_margin: float = 0.12
    rnn_confirmations: int = 2
    rnn_idle_confidence: float = 0.8

    def validate(self) -> "Config":
        if not isinstance(self.mapping, dict) or not self.mapping:
            raise ValueError("mapping 必须是非空对象")
        for event, action in self.mapping.items():
            if event not in EVENTS or not isinstance(action, str) or action not in ACTIONS:
                raise ValueError(f"无效映射 {event!r}={action!r}；单击保留给固件切换模式")
        if not isinstance(self.keys, dict) or set(self.keys) != ACTIONS:
            raise ValueError("keys 必须包含 next 和 previous，未启用的动作使用 null")
        enabled = []
        for key in self.keys.values():
            if key is not None and (not isinstance(key, str) or key not in KEY_CODES):
                raise ValueError(f"无效按键 {key!r}；仅支持 SPACE、F13～F20")
            if key is not None:
                enabled.append(key)
        if not enabled or len(set(enabled)) != len(enabled):
            raise ValueError("至少启用一个按键，且 next/previous 不能使用相同按键")
        limits = {
            "cooldown_s": (0.2, 10), "max_event_age_s": (0.05, 2),
            "settle_s": (0, 10), "scan_timeout_s": (1, 60),
            "connect_timeout_s": (5, 120), "command_timeout_s": (1, 30),
            "heartbeat_s": (5, 120), "key_hold_s": (0.02, 0.5),
            "rnn_confidence": (0.5, 1), "rnn_margin": (0, 1),
            "rnn_idle_confidence": (0.5, 1),
        }
        for name, (low, high) in limits.items():
            value = getattr(self, name)
            if (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or not low <= value <= high):
                raise ValueError(f"{name} 必须在 {low}～{high} 之间")
        if type(self.rnn_confirmations) is not int or not 1 <= self.rnn_confirmations <= 10:
            raise ValueError("rnn_confirmations 必须为 1～10 的整数")
        return self


def load_config(path: Path | None = None, preset: str | None = None) -> Config:
    values = {}
    if path is not None:
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, dict):
            raise ValueError("配置文件必须是 JSON 对象")
        unknown = set(values) - {f.name for f in fields(Config)}
        if unknown:
            raise ValueError(f"未知配置字段：{sorted(unknown)}")
    config = Config(**values)
    if preset == "button":
        config.mapping = {"key_double_press": "next"}
    elif preset == "gesture":
        config.mapping = {"rotate_front": "next", "rotate_back": "previous", "wave": "next"}
    elif preset == "rnn":
        config.mapping = {"up": "next", "down": "previous"}
    return config.validate()
