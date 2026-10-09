"""Douyin-only WDA controls. Loopback transport, no action retries or mirroring."""
from __future__ import annotations

import base64
import asyncio
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
import threading
import urllib.error
import urllib.request

from .storage import write_json

BUNDLE = "com.ss.iphone.ugc.Aweme"



class WDAError(RuntimeError):
    pass


class WDAOutcomeUnknown(WDAError):
    """A mutation may have reached WDA. Never replay it automatically."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTP:
    def __init__(self, port=18100, *, opener=None, timeout=45):
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ValueError("WDA 端口必须为 1024～65535")
        self.base = f"http://127.0.0.1:{port}"
        self.timeout = timeout
        # Local requests must not be sent through user/system HTTP proxies.
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, body=None):
        if method not in {"GET", "POST"} or not path.startswith("/") or ".." in path or "://" in path:
            raise ValueError("无效的 WDA 请求")
        encoded = None if body is None else json.dumps(body, allow_nan=False).encode()
        request = urllib.request.Request(self.base + path, data=encoded, method=method,
                                         headers={"Content-Type": "application/json", "Connection": "close"})
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                data = response.read(32 * 1024 * 1024 + 1)
            if len(data) > 32 * 1024 * 1024:
                raise ValueError("WDA 响应过大")
            result = json.loads(data)
            if not isinstance(result, dict) or "value" not in result:
                raise ValueError("WDA 响应格式无效")
        except (OSError, ValueError, urllib.error.URLError) as exc:
            error = WDAOutcomeUnknown if method == "POST" else WDAError
            raise error("WDA 请求未能确认结果；不会自动重试。保持 USB 转发和 Xcode 测试运行。") from exc
        value = result["value"]
        if isinstance(value, dict) and value.get("error"):
            raise WDAError(str(value.get("message") or value["error"]))
        return result


class Phone:
    def __init__(self, *, api=None, sleep=time.sleep):
        self.api = api if api is not None else HTTP()
        self.sleep = sleep
        self.sid = None
        self.mutations = []

    def require_app(self):
        path = f"/session/{self.sid}/wda/activeAppInfo" if self.sid else "/wda/activeAppInfo"
        response = self.api.request("GET", path)
        value = response.get("value")
        if not isinstance(value, dict) or value.get("bundleId") != BUNDLE:
            raise WDAError("当前应用不是抖音；请先打开抖音，不自动切换其他应用。")
        return response

    def attach(self):
        status = self.api.request("GET", "/status")["value"]
        if not isinstance(status, dict) or status.get("ready") is not True:
            raise WDAError("WDA 尚未就绪")
        active = self.require_app()
        sid = active.get("sessionId")
        if not sid:
            response = self.api.request("POST", "/session", {"capabilities": {"alwaysMatch": {
                "bundleId": BUNDLE, "forceAppLaunch": False, "shouldTerminateApp": False,
                "waitForIdleTimeout": 1.0}}})
            value = response.get("value")
            sid = response.get("sessionId") or (value.get("sessionId") if isinstance(value, dict) else None)
        if not isinstance(sid, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", sid):
            raise WDAError("WDA 未返回有效会话")
        self.sid = sid
        self.require_app()
        return self

    def session(self, method, path, body=None):
        if not self.sid:
            raise WDAError("尚未连接 WDA 会话")
        return self.api.request(method, f"/session/{self.sid}{path}", body)["value"]

    def mutate(self, path, body, *, label):
        self.require_app()
        entry = {"operation": label, "outcome": "unknown"}
        self.mutations.append(entry)
        value = self.session("POST", path, body)
        entry["outcome"] = "wda_accepted"
        return value

    def swipe(self, direction):
        if direction not in {"up", "down"}:
            raise ValueError("仅支持 up/down")
        self.mutate("/wda/swipe", {"direction": direction}, label=f"swipe_{direction}")
        return {"action": "next" if direction == "up" else "previous",
                "wda_accepted": True, "phone_effect": "visual_confirmation_required"}

    def capture(self, directory: Path):
        self.require_app()
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        settings = self.session("GET", "/appium/settings")
        if not isinstance(settings, dict) or type(settings.get("screenshotQuality")) is not int:
            raise WDAError("无法取得截图设置；不改变现有设置")
        quality = settings["screenshotQuality"]
        try:
            self.session("POST", "/appium/settings", {"settings": {"screenshotQuality": 0}})
            self.require_app()
            encoded = self.session("GET", "/screenshot")
            if not isinstance(encoded, str):
                raise WDAError("截图响应无效")
            image = base64.b64decode(encoded, validate=True)
            if not image.startswith(b"\x89PNG\r\n\x1a\n"):
                raise WDAError("截图未返回 PNG")
            source = self.session("GET", "/source")
            if not isinstance(source, str):
                raise WDAError("控件树响应无效")
            self.require_app()
            for name, data in (("screen.png", image), ("source.xml", source.encode())):
                path = directory / name
                with path.open("xb") as stream:
                    path.chmod(0o600)
                    stream.write(data)
        finally:
            self.session("POST", "/appium/settings", {"settings": {"screenshotQuality": quality}})


def phone_command(args):
    if args.config is not None:
        raise ValueError("phone 命令使用直接 WDA 操作，不读取戒指事件映射配置")
    actions = {"next": lambda phone: phone.swipe("up"), "previous": lambda phone: phone.swipe("down")}
    keys = {"u": "next", "d": "previous"}
    phone = None
    def run(action):
        nonlocal phone
        if args.dry_run or (not args.enable_output and action != "inspect"):
            print(json.dumps({"action": action, "mode": "dry-run", "phone_actions_sent": 0}))
            return
        if phone is None:
            phone = Phone(api=HTTP(args.port)).attach()
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        directory = args.state_dir / "wda" / stamp
        report = {"action": action, "iphone_mirroring": False, "status": "started"}
        first_mutation = len(phone.mutations)
        action_completed = False
        write_json(directory / "report.json", report)
        try:
            if args.capture and action != "inspect":
                phone.capture(directory / "before")
            if action == "inspect":
                phone.capture(directory / "inspect")
                report.update(status="captured", folder=str(directory))
            else:
                report.update(actions[action](phone), status="wda_accepted")
                action_completed = True
            if args.capture and action != "inspect":
                phone.capture(directory / "after")
        except BaseException as exc:
            report.update(status="cancelled" if isinstance(exc, KeyboardInterrupt) else "action_accepted_capture_failed" if action_completed else "failed",
                          phone_action_accepted=action_completed, error=f"{type(exc).__name__}: {exc}", retry_safe=False)
            if args.capture and not isinstance(exc, KeyboardInterrupt):
                try:
                    phone.capture(directory / "diagnostic")
                except Exception:
                    pass
            raise
        finally:
            report["mutations"] = list(phone.mutations[first_mutation:])
            write_json(directory / "report.json", report)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"本地报告：{directory}")
    if args.phone_action == "console":
        print("u+回车 下一条；d+回车 上一条；q+回车 退出")
        while True:
            try:
                command = input("phone> ").strip().lower()
            except EOFError:
                return
            if command == "q":
                return
            if command in keys:
                run(keys[command])
            else:
                print("支持 u / d / q")
    else:
        run(args.phone_action)


class WDAOutput:
    """Async bridge adapter. A submitted WDA request is never replayed."""
    trusted = True  # WDA transport does not require macOS Accessibility.

    def __init__(self, config, *, api=None, clock=time.monotonic):
        self.api = api or HTTP(config.wda_port, timeout=5)
        self.phone = Phone(api=self.api)
        self.clock = clock
        self.lock = asyncio.Lock()
        self.last_attempt = None

    async def prepare(self):
        await asyncio.to_thread(self.phone.attach)

    async def perform(self, action, *, guard=None, valid_until=None):
        from .mirroring import MirrorOutputInterrupted
        if action not in {'next', 'previous'}:
            raise ValueError('WDA 桥接仅支持 next/previous')
        if self.lock.locked():
            raise MirrorOutputInterrupted('WDA 输出繁忙，不排队旧动作')
        async with self.lock:
            revoked = threading.Event()
            attempt = {'action': action, 'transport': 'wda', 'event_kind': 'native_swipe',
                       'attempted_scroll_events': 0, 'submitted_scroll_events': 0,
                       'completed': False, 'outcome': 'not_sent'}
            self.last_attempt = attempt
            parent = self
            class GuardedAPI:
                def request(self, method, path, body=None):
                    if revoked.is_set() or (guard is not None and not guard()):
                        raise MirrorOutputInterrupted('WDA 输入已撤销')
                    touch = path.endswith('/wda/swipe') and method == 'POST'
                    if touch and valid_until is not None and parent.clock() > valid_until:
                        raise MirrorOutputInterrupted('WDA 输入已过期，未提交滑动')
                    if touch:
                        attempt['attempted_scroll_events'] += 1
                        attempt['outcome'] = 'unknown'
                    result = parent.api.request(method, path, body)
                    if touch:
                        attempt['submitted_scroll_events'] += 1
                    return result
            def worker():
                phone = Phone(api=GuardedAPI())
                phone.sid = self.phone.sid
                if not phone.sid:
                    phone.attach()
                    self.phone.sid = phone.sid
                phone.swipe('up' if action == 'next' else 'down')
                attempt['completed'] = not revoked.is_set()
                attempt['outcome'] = 'wda_accepted' if attempt['completed'] else 'unknown_after_cancel'
            task = asyncio.create_task(asyncio.to_thread(worker))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                revoked.set()
                try:
                    await asyncio.shield(task)
                except Exception:
                    pass
                attempt['completed'] = False
                if attempt['attempted_scroll_events']:
                    attempt['outcome'] = 'unknown_after_cancel'
                raise
