"""Foreground-only iPhone Mirroring output using unphased HID wheel bursts.

Implementation references and pinned revisions: docs/mirroring.md.
No upstream source is vendored. CGEvent pixel units are 0, not 1 (line units).
"""
from __future__ import annotations

import asyncio
import ctypes as C
from dataclasses import dataclass
import sys
import time
import math

from .output import MacKeyboard

MIRROR_BUNDLE = "com.apple.ScreenContinuity"
PIXEL_UNIT = 0
HID_EVENT_TAP = 0


def bind_scroll_creation(cg):
    """Avoid arm64 variadic ABI ambiguity; all six parameters are fixed."""
    function = cg.CGEventCreateScrollWheelEvent2
    function.argtypes = [C.c_void_p, C.c_uint32, C.c_uint32,
                         C.c_int32, C.c_int32, C.c_int32]
    function.restype = C.c_void_p


@dataclass(frozen=True)
class MirrorTarget:
    pid: int
    window_id: int
    x: float
    y: float
    width: float
    height: float

    def __post_init__(self):
        if type(self.pid) is not int or self.pid <= 0 or type(self.window_id) is not int or self.window_id <= 0:
            raise ValueError("无效的镜像进程或窗口编号")
        values = (self.x, self.y, self.width, self.height)
        if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) for value in values):
            raise ValueError("镜像窗口坐标必须有限")
        if self.width < 200 or self.height < 400 or not .3 <= self.width / self.height <= .7:
            raise ValueError("镜像窗口必须为可见竖屏尺寸")

    @property
    def anchor(self):
        # Center-left content, away from Douyin's right-hand action buttons.
        return self.x + .42 * self.width, self.y + .55 * self.height


def scroll_deltas(action: str, pixels: int, *, natural: bool, invert: bool,
                  steps: int = 12) -> list[int]:
    if action not in {"next", "previous"}:
        raise ValueError("镜像仅支持 next/previous")
    if type(pixels) is not int or not 120 <= pixels <= 1200:
        raise ValueError("镜像滚动距离必须为 120～1200 像素")
    if type(steps) is not int or not 2 <= steps <= 40:
        raise ValueError("无效的滚动步数")
    if type(natural) is not bool or type(invert) is not bool:
        raise ValueError("滚动方向参数必须为布尔值")
    sign = (-1 if natural else 1) * (-1 if action == "previous" else 1) * (-1 if invert else 1)
    base, remainder = divmod(pixels, steps)
    return [sign * (base + (i < remainder)) for i in range(steps)]


class _Point(C.Structure):
    _fields_ = [("x", C.c_double), ("y", C.c_double)]


class _Size(C.Structure):
    _fields_ = [("width", C.c_double), ("height", C.c_double)]


class _Rect(C.Structure):
    _fields_ = [("origin", _Point), ("size", _Size)]


class NativeMirrorAPI:
    def __init__(self):
        if sys.platform != "darwin":
            raise RuntimeError("iPhone 镜像输出仅支持 macOS")
        from AppKit import NSWorkspace
        from Foundation import NSRunLoop, NSDate, NSUserDefaults
        self.workspace = NSWorkspace.sharedWorkspace()
        self.run_loop, self.date = NSRunLoop.currentRunLoop(), NSDate
        self.defaults = NSUserDefaults.standardUserDefaults()
        self.keyboard = MacKeyboard()
        self.ax = self.keyboard.api
        self.cg = C.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
        self.cf = C.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        signatures = [
            (self.cg, "CGWindowListCopyWindowInfo", [C.c_uint32, C.c_uint32], C.c_void_p),
            (self.cg, "CGRectMakeWithDictionaryRepresentation", [C.c_void_p, C.POINTER(_Rect)], C.c_bool),
            (self.cg, "CGEventCreateMouseEvent", [C.c_void_p, C.c_uint32, _Point, C.c_uint32], C.c_void_p),
            (self.cg, "CGEventCreate", [C.c_void_p], C.c_void_p),
            (self.cg, "CGEventGetLocation", [C.c_void_p], _Point),
            (self.cg, "CGEventSetFlags", [C.c_void_p, C.c_uint64], None),
            (self.cg, "CGEventPost", [C.c_uint32, C.c_void_p], None),
            (self.cf, "CFArrayGetCount", [C.c_void_p], C.c_long),
            (self.cf, "CFArrayGetValueAtIndex", [C.c_void_p, C.c_long], C.c_void_p),
            (self.cf, "CFDictionaryGetValue", [C.c_void_p, C.c_void_p], C.c_void_p),
            (self.cf, "CFStringCreateWithCString", [C.c_void_p, C.c_char_p, C.c_uint32], C.c_void_p),
            (self.cf, "CFNumberGetValue", [C.c_void_p, C.c_int, C.c_void_p], C.c_bool),
            (self.cf, "CFRelease", [C.c_void_p], None),
            (self.cf, "CFGetTypeID", [C.c_void_p], C.c_ulong),
            (self.cf, "CFArrayGetTypeID", [], C.c_ulong),
            (self.cf, "CFStringGetTypeID", [], C.c_ulong),
            (self.cf, "CFStringGetCString", [C.c_void_p, C.c_char_p, C.c_long, C.c_uint32], C.c_bool),
            (self.ax, "AXUIElementCreateApplication", [C.c_int], C.c_void_p),
            (self.ax, "AXUIElementCopyAttributeValue", [C.c_void_p, C.c_void_p, C.POINTER(C.c_void_p)], C.c_int32),
        ]
        for lib, name, args, result in signatures:
            function = getattr(lib, name)
            function.argtypes, function.restype = args, result
        bind_scroll_creation(self.cg)

    def _ax_copy(self, element, name, *, optional=False):
        key = self.cf.CFStringCreateWithCString(None, name.encode(), 0x08000100)
        if not key:
            raise RuntimeError("AX 属性分配失败")
        value = C.c_void_p()
        try:
            error = self.ax.AXUIElementCopyAttributeValue(element, key, C.byref(value))
        finally:
            self.cf.CFRelease(key)
        if error:
            if value.value:
                self.cf.CFRelease(value.value)
            if optional and error in (-25205, -25212):  # unsupported / no value
                return None
            raise RuntimeError("无法核验镜像连接页面；未发送输入")
        return value.value

    def _ax_text(self, element, name):
        value = self._ax_copy(element, name, optional=True)
        if not value:
            return ""
        try:
            if self.cf.CFGetTypeID(value) != self.cf.CFStringGetTypeID():
                return ""
            buffer = C.create_string_buffer(2048)
            if not self.cf.CFStringGetCString(value, buffer, len(buffer), 0x08000100):
                raise RuntimeError("无法读取镜像原生提示")
            return buffer.value.decode("utf-8")
        finally:
            self.cf.CFRelease(value)

    def _native_controls(self, pid):
        app = self.ax.AXUIElementCreateApplication(pid)
        if not app:
            raise RuntimeError("无法核验镜像连接页面")
        controls, seen = [], set()
        def children(element, attribute):
            array = self._ax_copy(element, attribute, optional=attribute == "AXChildren")
            if not array:
                return
            try:
                if self.cf.CFGetTypeID(array) != self.cf.CFArrayGetTypeID():
                    raise RuntimeError("镜像 AX 子元素类型无效")
                for index in range(self.cf.CFArrayGetCount(array)):
                    yield self.cf.CFArrayGetValueAtIndex(array, index)
            finally:
                self.cf.CFRelease(array)
        def visit(element, depth):
            if element in seen:
                return
            if depth > 6 or len(seen) >= 128:
                raise RuntimeError("镜像原生页面结构超出可核验范围")
            seen.add(element)
            role = self._ax_text(element, "AXRole")
            if not role:
                raise RuntimeError("镜像 AX 元素缺少角色；无法核验连接")
            if role == "AXToolbar":
                return  # Native window toolbar is not mirrored phone content.
            subrole = self._ax_text(element, "AXSubrole")
            controls.append((role, subrole))
            for child in children(element, "AXChildren"):
                visit(child, depth + 1)
        try:
            for window in children(app, "AXWindows"):
                visit(window, 0)
            if not controls:
                raise RuntimeError("镜像窗口无可核验的 AX 结构")
            return controls
        finally:
            self.cf.CFRelease(app)

    def _assert_connection_ready(self, pid):
        assert_connection_controls(self._native_controls(pid))

    def _pump(self):
        # asyncio alone does not update NSWorkspace's foreground-app cache.
        self.run_loop.runUntilDate_(self.date.dateWithTimeIntervalSinceNow_(.001))

    @property
    def trusted(self):
        return self.keyboard.trusted

    @property
    def natural_scrolling(self):
        value = self.defaults.objectForKey_("com.apple.swipescrolldirection")
        return True if value is None else bool(value)

    def target(self):
        self._pump()
        app = self.workspace.frontmostApplication()
        if not app or str(app.bundleIdentifier()) != MIRROR_BUNDLE:
            raise RuntimeError("请将已连接手机的 iPhone 镜像窗口置于前台；未向其他应用发送输入")
        pid = int(app.processIdentifier())
        self._assert_connection_ready(pid)
        array = self.cg.CGWindowListCopyWindowInfo(1 | 16, 0)
        if not array:
            raise RuntimeError("无法读取镜像窗口")
        keys = {}
        try:
            for key in ("kCGWindowOwnerPID", "kCGWindowLayer", "kCGWindowBounds", "kCGWindowNumber"):
                keys[key] = self.cf.CFStringCreateWithCString(None, key.encode(), 0x08000100)
                if not keys[key]:
                    raise RuntimeError("窗口键分配失败")
            candidates = []
            for index in range(self.cf.CFArrayGetCount(array)):
                row = self.cf.CFArrayGetValueAtIndex(array, index)
                def integer(key):
                    value = self.cf.CFDictionaryGetValue(row, keys[key])
                    out = C.c_int()
                    return out.value if value and self.cf.CFNumberGetValue(value, 9, C.byref(out)) else None
                if integer("kCGWindowOwnerPID") != pid or integer("kCGWindowLayer") != 0:
                    continue
                rect = _Rect()
                bounds = self.cf.CFDictionaryGetValue(row, keys["kCGWindowBounds"])
                if bounds and self.cg.CGRectMakeWithDictionaryRepresentation(bounds, C.byref(rect)):
                    width, height = rect.size.width, rect.size.height
                    number = integer("kCGWindowNumber")
                    if width >= 200 and height >= 400 and .3 <= width / height <= .7 and number is not None:
                        candidates.append(MirrorTarget(pid, number, rect.origin.x, rect.origin.y, width, height))
            if len(candidates) != 1:
                raise RuntimeError("需要唯一、可见的竖屏镜像窗口；请先连接 iPhone 并关闭弹窗")
            return candidates[0]
        finally:
            for key in filter(None, keys.values()):
                self.cf.CFRelease(key)
            self.cf.CFRelease(array)

    async def focus(self):
        apps = [app for app in self.workspace.runningApplications()
                if str(app.bundleIdentifier()) == MIRROR_BUNDLE and not app.isTerminated()]
        if len(apps) != 1 or not apps[0].activateWithOptions_(0):
            raise RuntimeError("请先打开并连接 iPhone 镜像")
        for _ in range(20):
            try:
                return self.target()
            except RuntimeError:
                await asyncio.sleep(.05)
        return self.target()

    def move(self, point):
        event = self.cg.CGEventCreateMouseEvent(None, 5, _Point(*point), 0)
        self._post(event)

    def pointer(self):
        event = self.cg.CGEventCreate(None)
        if not event:
            raise RuntimeError("无法核验系统指针位置")
        try:
            point = self.cg.CGEventGetLocation(event)
            return point.x, point.y
        finally:
            self.cf.CFRelease(event)

    def scroll(self, delta):
        # Deliberately no isContinuous, scrollPhase, or momentumPhase overrides.
        # Several Mirroring implementations report synthetic touch phases can
        # leave an active gesture and make following taps ineffective.
        event = self.cg.CGEventCreateScrollWheelEvent2(None, PIXEL_UNIT, 1, delta, 0, 0)
        self._post(event)

    def _post(self, event):
        if not event:
            raise RuntimeError("CGEvent 分配失败；停止且不重试")
        try:
            self.cg.CGEventSetFlags(event, 0)
            self.cg.CGEventPost(HID_EVENT_TAP, event)
        finally:
            self.cf.CFRelease(event)


class MacMirroring:
    def __init__(self, config, *, api=None, sleep=asyncio.sleep, clock=time.monotonic):
        self.config = config
        self.api = api if api is not None else NativeMirrorAPI()
        self.sleep = sleep
        self.clock = clock
        self._lock = asyncio.Lock()
        self.last_attempt = None

    @property
    def trusted(self):
        return self.api.trusted

    async def focus(self):
        if not self.trusted:
            raise PermissionError("缺少 macOS 辅助功能权限")
        return await self.api.focus()

    async def perform(self, action, *, guard=None, valid_until=None):
        if valid_until is not None and (isinstance(valid_until, bool) or not isinstance(valid_until, (int, float)) or not math.isfinite(valid_until)):
            raise ValueError("输入有效期必须为有限时间")
        if self._lock.locked():
            raise MirrorOutputInterrupted("镜像输出繁忙；不排队旧指令")
        async with self._lock:
            attempt = {"action": action, "attempted_scroll_events": 0,
                       "submitted_scroll_events": 0, "submitted_pixel_delta": 0,
                       "pointer_moved": False, "completed": False, "outcome": "not_sent"}
            self.last_attempt = attempt
            def check_input():
                if guard is not None and not guard():
                    raise MirrorOutputInterrupted("输入已被暂停、断线或会话变更撤销")
                if not attempt["attempted_scroll_events"] and valid_until is not None and self.clock() > valid_until:
                    raise MirrorOutputInterrupted("输入已过期；未开始滚动")
            try:
                if not self.trusted:
                    raise PermissionError("缺少 macOS 辅助功能权限")
                check_input()
                deltas = scroll_deltas(action, self.config.mirror_scroll_pixels,
                                       natural=self.api.natural_scrolling,
                                       invert=self.config.mirror_invert_scroll)
                target = self.api.target()
                check_input()
                self.api.move(target.anchor)
                attempt["pointer_moved"] = True
                await self.sleep(.05)
                for index, delta in enumerate(deltas):
                    check_input()
                    if not self.trusted or self.api.target() != target:
                        raise RuntimeError("镜像目标、焦点或权限发生变化；停止且不重放动作")
                    pointer = self.api.pointer()
                    if len(pointer) != 2 or any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in pointer):
                        raise RuntimeError("系统指针坐标无效；未继续滚动")
                    if math.hypot(pointer[0] - target.anchor[0], pointer[1] - target.anchor[1]) > 12:
                        raise RuntimeError("系统指针已移动；停止滚动，避免发送到其他窗口")
                    attempt["attempted_scroll_events"] += 1
                    self.api.scroll(delta)
                    attempt["submitted_scroll_events"] += 1
                    attempt["submitted_pixel_delta"] += delta
                    if index + 1 < len(deltas):
                        await self.sleep(self.config.mirror_scroll_duration_s / (len(deltas) - 1))
                attempt["completed"] = True
            finally:
                attempt["outcome"] = ("locally_submitted" if attempt["completed"] else
                                      "unknown" if attempt["attempted_scroll_events"] else "not_sent")


class DryMirroring:
    def __init__(self):
        self.count = 0

    async def perform(self, action, **kwargs):
        if action not in {"next", "previous"}:
            raise ValueError("无效的模拟动作")
        self.count += 1


class MirrorOutputInterrupted(RuntimeError):
    """No retry: a current input was revoked or would have queued."""


def assert_connection_controls(controls):
    # Current connected Mirroring content is opaque to native AX. Connection,
    # lock/in-use and permission pages expose native text/buttons instead.
    # Fail closed if a new OS starts exposing phone controls; do not infer ready.
    chrome = {"AXCloseButton", "AXMinimizeButton", "AXZoomButton", "AXFullScreenButton"}
    if not controls or any(role in {"AXSheet", "AXDialog", "AXStaticText"} or
                           (role == "AXButton" and subrole not in chrome)
                           for role, subrole in controls):
        raise RuntimeError("镜像仍有原生提示、连接页或不受支持的 AX 内容；未发送输入")
