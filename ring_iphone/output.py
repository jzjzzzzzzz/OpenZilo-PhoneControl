from __future__ import annotations

import asyncio
import ctypes
import sys

from .config import KEY_CODES


class MacKeyboard:
    """Posts one down/up pair. This cannot acknowledge delivery to an iPhone."""
    def __init__(self, hold_s: float = 0.06):
        if sys.platform != "darwin":
            raise RuntimeError("真实按键输出仅支持 macOS")
        self.hold_s = hold_s
        self.api = ctypes.CDLL("/System/Library/Frameworks/ApplicationServices.framework/ApplicationServices")
        self.cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        self.api.AXIsProcessTrusted.argtypes = ()
        self.api.AXIsProcessTrusted.restype = ctypes.c_bool
        self.api.CGEventCreateKeyboardEvent.argtypes = (ctypes.c_void_p, ctypes.c_uint16, ctypes.c_bool)
        self.api.CGEventCreateKeyboardEvent.restype = ctypes.c_void_p
        self.api.CGEventSetFlags.argtypes = (ctypes.c_void_p, ctypes.c_uint64)
        self.api.CGEventSetFlags.restype = None
        self.api.CGEventPost.argtypes = (ctypes.c_uint32, ctypes.c_void_p)
        self.api.CGEventPost.restype = None
        self.cf.CFRelease.argtypes = (ctypes.c_void_p,)
        self.cf.CFRelease.restype = None

    @property
    def trusted(self) -> bool:
        return bool(self.api.AXIsProcessTrusted())

    async def press(self, key: str) -> None:
        if key not in KEY_CODES:
            raise ValueError(f"不支持按键：{key}")
        if not self.trusted:
            raise PermissionError("缺少辅助功能权限：系统设置 → 隐私与安全性 → 辅助功能，允许当前 Terminal/Codex/Python")
        down = self.api.CGEventCreateKeyboardEvent(None, KEY_CODES[key], True)
        up = self.api.CGEventCreateKeyboardEvent(None, KEY_CODES[key], False)
        if not down or not up:
            for event in (down, up):
                if event:
                    self.cf.CFRelease(event)
            raise RuntimeError("CGEventCreateKeyboardEvent 失败；未发送按键")
        try:
            self.api.CGEventSetFlags(down, 0)
            self.api.CGEventSetFlags(up, 0)
            try:
                self.api.CGEventPost(0, down)
                await asyncio.sleep(self.hold_s)
            finally:
                # Release even when Ctrl-C/cancellation arrives during key-down.
                self.api.CGEventPost(0, up)
        finally:
            self.cf.CFRelease(down)
            self.cf.CFRelease(up)


class DryKeyboard:
    def __init__(self):
        self.count = 0

    async def press(self, key: str) -> None:
        self.count += 1
