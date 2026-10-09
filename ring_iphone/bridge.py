from __future__ import annotations

import asyncio
from dataclasses import asdict
import signal
import sys
import time

from . import APP_NAME
from .ble import LinkLost, connect_profile, verify_identity
from .events import EventGate, RingEvent
from .output import DryKeyboard, MacKeyboard
from .storage import read_profile, utc_now, write_json


async def run_bridge(config, state, logger, *, enable_output=False, monitor=False, duration=None,
                     connector=connect_profile, keyboard=None, artifact=None):
    profile = read_profile(state / "ring-profile.json")
    if config.output_backend == "wda":
        from .wda import WDAOutput
        from .mirroring import DryMirroring
        output = keyboard or (WDAOutput(config) if enable_output else DryMirroring())
    elif config.output_backend == "mirroring":
        from .mirroring import MacMirroring, DryMirroring
        output = keyboard or (MacMirroring(config) if enable_output else DryMirroring())
    else:
        output = keyboard or (MacKeyboard(config.key_hold_s) if enable_output else DryKeyboard())
    if enable_output and not output.trusted:
        raise PermissionError("缺少 macOS 辅助功能权限；运行 ./ringphone doctor 查看状态")
    gate = EventGate(config)
    motion = None
    if artifact is not None:
        from .models import MotionStream
        motion = MotionStream(artifact, config)
    status = {
        "schema": "ring-iphone/v1", "application": APP_NAME, "started_at": utc_now(), "state": "starting",
        "mode": "monitor" if monitor else ("live" if enable_output else "dry-run"),
        "connected": False, "paused": False, "session": 0,
        "posted_keys": 0, "simulated_keys": 0, "last_event": None, "last_output": None,
        "iphone_delivery": "unverified", "mapping": config.mapping, "keys": config.keys,
        "source": "rnn" if artifact else "events",
        "output_backend": config.output_backend, "posted_swipes": 0, "simulated_swipes": 0,
        "partial_swipes": 0, "last_output_attempt": None,
    }
    path = state / "status.json"

    def update(**changes):
        status.update(changes)
        status["updated_at"] = utc_now()
        write_json(path, status)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def console_command():
        line = sys.stdin.readline()
        if not line:
            loop.remove_reader(sys.stdin.fileno())
            return
        command = line.strip().lower()
        if command == "p":
            gate.pause()
            if motion:
                motion.reset()
            update(paused=True)
            logger.info("已暂停输出（BLE 继续接收）")
        elif command == "r":
            gate.resume(time.monotonic())
            if motion:
                motion.reset()
            update(paused=False)
            logger.info("恢复输出，等待 %.1f 秒后接受新动作", config.settle_s)
        elif command == "q":
            stop.set()

    async def handle_event(event, link):
        decision = gate.handle(event, time.monotonic())
        if link.disconnected.is_set():
            return
        update(last_event={**asdict(event), **asdict(decision)}, transport=link.stats.copy())
        if monitor:
            logger.info("事件 %s t=%d（仅监控）", event.name, event.timestamp_ms)
        elif decision.reason == "accepted":
            # Output failures propagate and stop the bridge. Never retry a phone action.
            mirrored = config.output_backend in {"mirroring", "wda"}
            if mirrored:
                from .mirroring import MirrorOutputInterrupted
                generation = gate.generation
                try:
                    await output.perform(decision.action,
                        guard=lambda: not gate.paused and gate.generation == generation and not link.disconnected.is_set(),
                        valid_until=event.received_at + config.max_event_age_s)
                except MirrorOutputInterrupted as exc:
                    logger.info("丢弃已撤销的镜像动作：%s", exc)
                    return
                finally:
                    attempt = getattr(output, "last_attempt", None)
                    if attempt is not None:
                        if attempt["attempted_scroll_events"] and not attempt["completed"]:
                            status["partial_swipes"] += 1
                        update(last_output_attempt={"at": utc_now(), **attempt})
            else:
                await output.press(decision.key)
            field = ("posted_swipes" if enable_output else "simulated_swipes") if mirrored else ("posted_keys" if enable_output else "simulated_keys")
            status[field] += 1
            update(last_output={"at": utc_now(), "event": event.name, "action": decision.action,
                                "key": decision.key, "backend": config.output_backend,
                                "kind": "posted" if enable_output else "simulated"})
            logger.info("%s %s → %s → %s（手机端执行未确认）",
                        "POST" if enable_output else "DRY", event.name, decision.action,
                        config.output_backend if mirrored else decision.key)
        else:
            logger.info("忽略 %s：%s", event.name, decision.reason)

    async def consume(link):
        while True:
            await handle_event(await link.events.get(), link)

    async def consume_rnn(link):
        while True:
            try:
                batch, received_at = await asyncio.wait_for(link.imu.get(), timeout=5.0)
            except TimeoutError as exc:
                raise LinkLost("IMU 数据超时；检查戒指是否仍处于手势模式") from exc
            if (gate.paused or received_at < gate.ready_at
                    or time.monotonic() - received_at > config.max_event_age_s):
                motion.reset()
                continue
            window = motion.prepare(batch)
            if window is None:
                continue
            generation = motion.generation
            probabilities = await asyncio.to_thread(artifact.predict, window)
            if (generation != motion.generation or link.disconnected.is_set() or gate.paused
                    or time.monotonic() - received_at > config.max_event_age_s):
                motion.reset()
                continue
            label = motion.observe(probabilities)
            update(last_prediction={"probabilities": probabilities, "armed": motion.armed},
                   transport=link.stats.copy())
            if label:
                await handle_event(RingEvent(label, batch.samples[-1].timestamp_ms, received_at), link)

    async def heartbeat(link):
        while True:
            await asyncio.sleep(config.heartbeat_s)
            try:
                info = await link.request_info()
                verify_identity(info, profile["cpuid"])
            except Exception as exc:
                raise LinkLost(f"链路心跳失败：{exc}") from exc
            update(battery_percent=info.battery_percent, transport=link.stats.copy())

    async def connected_session(link):
        consumer = asyncio.create_task(consume_rnn(link) if motion else consume(link))
        pulse = asyncio.create_task(heartbeat(link))
        lost = asyncio.create_task(link.disconnected.wait())
        tasks = {consumer, pulse, lost}
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            # Prioritize output exceptions over simultaneous disconnection.
            if consumer in done:
                consumer.result()
            if pulse in done:
                pulse.result()
            raise LinkLost("BLE 断开")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def connect_loop():
        delay = 1.0
        while True:
            update(state="connecting", connected=False)
            try:
                link, info = await connector(profile, config, logger)
            except Exception as exc:
                logger.info("连接失败：%s；%.0f 秒后重试", exc, delay)
                update(state="reconnecting", error=str(exc))
                await asyncio.sleep(delay)
                delay = min(15.0, delay * 2)
                continue
            try:
                delay = 1.0
                profile.update(address=link.candidate.address, firmware_version=info.firmware_version,
                               last_verified_at=utc_now())
                write_json(state / "ring-profile.json", profile)
                gate.reset(time.monotonic())
                link.activate()
                if motion:
                    motion.reset()
                    sensor = await link.start_imu()
                    artifact.check_sensor(sensor)
                    update(sensor=asdict(sensor), model=artifact.manifest["name"])
                update(state="running", connected=True, error=None,
                       session=status["session"] + 1, firmware_version=info.firmware_version,
                       battery_percent=info.battery_percent)
                logger.info("身份验证通过：固件 %s，电量 %s%%；稳定等待 %.1f 秒",
                            info.firmware_version, info.battery_percent, config.settle_s)
                try:
                    await connected_session(link)
                except LinkLost as exc:
                    logger.info("%s；清空旧事件并重新连接", exc)
                    update(state="reconnecting", connected=False, error=str(exc))
            finally:
                await link.close()
            await asyncio.sleep(delay)

    update()
    reader_added = False
    tasks = []
    installed_signals = []
    try:
        if sys.stdin.isatty():
            loop.add_reader(sys.stdin.fileno(), console_command)
            reader_added = True
        for number in (signal.SIGINT, signal.SIGTERM):
            previous_handler = signal.getsignal(number)
            loop.add_signal_handler(number, stop.set)
            installed_signals.append((number, previous_handler))
        logger.info("%s；p+回车暂停，r+回车恢复，q+回车或 Ctrl-C 退出",
                    "真实按键输出已启用" if enable_output else "仅监控" if monitor else "DRY-RUN，不发送按键")
        async def start_worker():
            if config.output_backend == 'wda' and enable_output and not monitor:
                await output.prepare()
            await connect_loop()
        worker = asyncio.create_task(start_worker())
        stopper = asyncio.create_task(stop.wait())
        tasks = [worker, stopper]
        if duration is not None:
            tasks.append(asyncio.create_task(asyncio.sleep(duration)))
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        if worker in done:
            worker.result()
    except BaseException as exc:
        update(error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if reader_added:
            loop.remove_reader(sys.stdin.fileno())
        for number, previous_handler in installed_signals:
            loop.remove_signal_handler(number)
            signal.signal(number, previous_handler)
        update(state="stopped", connected=False, stopped_at=utc_now())
        logger.info("已停止；不会重放未完成动作")
    return status
