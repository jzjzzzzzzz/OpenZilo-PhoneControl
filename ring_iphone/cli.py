from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from importlib import metadata
import json
import math
import os
from pathlib import Path
import sys

from . import APP_NAME, __version__
from .ble import RingLink, scan
from .bridge import run_bridge
from .config import KEY_CODES, ROOT, load_config
from .events import EventGate, RingEvent
from .output import MacKeyboard
from .sdk import load_sdk
from .storage import InstanceLock, make_logger, read_profile, utc_now, write_json


def positive(value: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("必须为有限正数")
    return number


def build_parser():
    parser = argparse.ArgumentParser(description=f"{APP_NAME}：戒指 → Mac 切换控制 → iPhone 抖音（本地运行）")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    help_text = {
        "doctor": "只读检查环境及辅助功能权限", "setup": "显示手机与 Mac 配置步骤",
        "scan": "扫描 NUS 设备", "pair": "核对物理 CPUID 并绑定",
        "monitor": "接收事件，不发送按键", "run": "运行桥接（默认 dry-run）",
        "test-key": "倒计时后发送一次测试按键，不连接戒指",
        "status": "读取最后状态快照（不是实时手机确认）",
        "replay": "离线回放样例，只模拟输出",
        "model": "导入或检查本地 Motion Lab RNN（不上传权重）",
    }
    sub = {}
    for name, help_message in help_text.items():
        p = commands.add_parser(name, help=help_message)
        p.add_argument("--state-dir", type=Path, default=ROOT / "state")
        p.add_argument("--config", type=Path, help="JSON 配置，省略则使用按键双击 → 下一条")
        sub[name] = p
    sub["pair"].add_argument("--address", help="scan 输出的 macOS CoreBluetooth UUID")
    sub["pair"].add_argument("--cpuid", help="只接受指定物理戒指")
    sub["pair"].add_argument("--profile", type=Path, help="导入已有绑定文件并重新实机验证；不改源文件")
    sub["pair"].add_argument("--replace", action="store_true", help="允许改绑为另一枚戒指")
    for name in ("run", "monitor"):
        sub[name].add_argument("--duration", type=positive, help="运行秒数，省略则直到退出")
        sub[name].add_argument("--preset", choices=("button", "gesture", "rnn"), help="覆盖配置中的事件映射")
        sub[name].add_argument("--source", choices=("events", "rnn"), default="events")
        sub[name].add_argument("--model", help="已导入的 RNN 名称，配合 --source rnn")
        sub[name].add_argument("--models-dir", type=Path, default=ROOT / "models")
    mode = sub["run"].add_mutually_exclusive_group()
    mode.add_argument("--enable-output", action="store_true", help="明确启用真实 Mac 按键输出")
    mode.add_argument("--dry-run", action="store_true", help="仅模拟输出（默认）")
    sub["test-key"].add_argument("--key", choices=tuple(KEY_CODES), default="SPACE")
    sub["test-key"].add_argument("--delay", type=positive, default=5.0)
    sub["test-key"].add_argument("--dry-run", action="store_true")
    sub["replay"].add_argument("path", nargs="?", type=Path, default=ROOT / "examples" / "events.jsonl")
    model_commands = sub["model"].add_subparsers(dest="model_command", required=True)
    importer = model_commands.add_parser("import", help="复制权重到本地忽略目录并校验推理")
    importer.add_argument("path", type=Path)
    importer.add_argument("--name", default="gesture-rnn")
    importer.add_argument("--lab", type=Path, required=True, help="本机可信的 combodied-motion-lab 根目录")
    importer.add_argument("--sample-rate", type=positive, required=True)
    importer.add_argument("--accel-range", type=positive, required=True)
    importer.add_argument("--gyro-range", type=positive, required=True)
    importer.add_argument("--replace", action="store_true")
    importer.add_argument("--allow-window-v4", action="store_true")
    inspector = model_commands.add_parser("inspect", help="核验权重摘要、运行时版本与模型契约")
    inspector.add_argument("name")
    for child in (importer, inspector):
        child.add_argument("--models-dir", type=Path, default=ROOT / "models")
    return parser


def doctor(state: Path) -> int:
    checks = {"application": APP_NAME, "version": __version__, "python": sys.version.split()[0], "executable": sys.executable,
              "macOS": sys.platform == "darwin",
              "profile_exists": (state / "ring-profile.json").exists(),
              "iphone_delivery": "需完成 test-key 实机验收，程序不能自动确认手机动作"}
    ready = True
    try:
        checks["sdk_version"] = load_sdk().__version__
        checks["sdk_path"] = load_sdk().__file__
        checks["bleak"] = metadata.version("bleak")
        from bleak import BleakClient  # Also catches missing CoreBluetooth dependencies.
        del BleakClient
        if not 3 <= int(checks["bleak"].split(".")[0]) < 4:
            checks["dependency_error"] = "需要 bleak>=3.0,<4；执行 ./setup.sh"
            ready = False
    except Exception as exc:
        checks["dependency_error"] = str(exc)
        ready = False
    try:
        checks["accessibility_trusted"] = MacKeyboard().trusted if sys.platform == "darwin" else False
    except Exception as exc:
        checks["accessibility_error"] = str(exc)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    print("检查不发送按键、不扫描蓝牙、不更改系统设置。辅助功能未授权不影响 dry-run。")
    return 0 if ready else 1


async def pair(args, config, logger):
    path = args.state_dir / "ring-profile.json"
    existing = read_profile(path) if path.exists() else None
    if args.profile and (args.address or args.cpuid):
        raise ValueError("--profile 不与 --address/--cpuid 同时使用")
    source = (read_profile(args.profile) if args.profile else existing if not args.replace else None)
    expected = args.cpuid or (source["cpuid"] if source else None)
    if existing and expected and existing["cpuid"].casefold() != expected.casefold() and not args.replace:
        raise ValueError("与已有绑定不同；确认更换戒指后使用 --replace")
    candidates = await scan(config.scan_timeout_s)
    if args.address:
        candidates = [c for c in candidates if c.address.casefold() == args.address.casefold()]
    if source:
        candidates.sort(key=lambda c: c.address.casefold() == source["address"].casefold(), reverse=True)
    if not candidates:
        raise ValueError("未发现目标 NUS 戒指；确认已开机、未被其他程序连接，然后重试")
    if len(candidates) > 1 and expected is None and not args.address:
        raise ValueError("发现多枚 NUS 设备；先 scan，再用 pair --address UUID 或 --cpuid 明确选择")
    for candidate in candidates:
        link = RingLink(candidate, config, logger=logger)
        try:
            info = await link.connect(expected)
        except Exception as exc:
            logger.info("候选设备未通过：%s", exc)
            continue
        try:
            if existing and info.cpuid.casefold() != existing["cpuid"].casefold() and not args.replace:
                raise ValueError("更换绑定需要 --replace")
            now = utc_now()
            profile = {"schema_version": 1, "address": candidate.address, "name": candidate.name,
                       "cpuid": info.cpuid, "model": info.model, "sn": info.sn,
                       "firmware_version": info.firmware_version,
                       "paired_at": existing.get("paired_at", now) if existing and
                       existing["cpuid"].casefold() == info.cpuid.casefold() else now,
                       "last_verified_at": now}
            write_json(path, profile)
            print(f"已验证并绑定 {info.model}，固件 {info.firmware_version}，电量 {info.battery_percent}%")
            print(f"绑定文件仅保存在：{path}")
            return
        finally:
            await link.close()
    raise ValueError("所有候选设备均未通过连接或 CPUID 验证，未更改绑定")


def replay(path: Path, config):
    gate = EventGate(config)
    gate.reset(0.0)
    last_at = -1.0
    accepted = 0
    for index, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError(f"第 {index} 行不是 JSON 对象")
        at = row["at_s"]
        stamp = row["timestamp_ms"]
        age = row.get("age_s", 0.0)
        for value in (at, age):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"第 {index} 行时间无效")
        if at < last_at or not isinstance(stamp, int) or isinstance(stamp, bool) or not 0 <= stamp <= 0xFFFFFFFF:
            raise ValueError(f"第 {index} 行时间倒序或设备 timestamp_ms 无效")
        if not isinstance(row.get("event"), str):
            raise ValueError(f"第 {index} 行 event 必须为字符串")
        last_at = at
        event = RingEvent(row["event"], stamp, at - age)
        decision = gate.handle(event, at)
        accepted += decision.key is not None
        print(json.dumps({"at_s": at, "event": event.name, **asdict(decision)}, ensure_ascii=False))
    print(f"回放完成：{accepted} 次模拟输出；未连接蓝牙，未发送任何真实按键。")


async def async_command(args, config, logger):
    if args.command == "scan":
        candidates = await scan(config.scan_timeout_s)
        for candidate in candidates:
            print(f"{candidate.name:16s} {candidate.rssi:4d} dBm  {candidate.address}")
        if not candidates:
            print("未发现广播 NUS 服务的戒指；确认已开机、靠近 Mac，且没有被其他程序连接。")
    elif args.command == "pair":
        await pair(args, config, logger)
    elif args.command in {"run", "monitor"}:
        artifact = None
        if args.source == "rnn":
            if not args.model:
                raise ValueError("--source rnn 需要指定 --model NAME")
            from .models import load_imported
            artifact = load_imported(args.model, args.models_dir)
            if not (set(config.mapping) & set(artifact.manifest["classes"])):
                raise ValueError("配置中的手势与 RNN 标签没有交集；请使用 --preset rnn 或 --config")
        elif args.model:
            raise ValueError("--model 仅用于 --source rnn")
        await run_bridge(config, args.state_dir, logger,
                         enable_output=getattr(args, "enable_output", False),
                         monitor=args.command == "monitor", duration=args.duration, artifact=artifact)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    os.umask(0o077)
    try:
        config = load_config(args.config, getattr(args, "preset", None))
        if args.command == "doctor":
            return doctor(args.state_dir)
        if args.command == "setup":
            print((ROOT / "docs/setup-iphone.zh-CN.md").read_text(encoding="utf-8"))
            return 0
        if args.command == "status":
            path = args.state_dir / "status.json"
            print(path.read_text(encoding="utf-8") if path.exists() else "尚无运行状态")
            print("这是最后一次本地快照；请查看 updated_at。posted_keys 不等于手机已翻页。")
            return 0
        if args.command == "replay":
            replay(args.path, config)
            return 0
        if args.command == "model":
            from .models import import_model, load_imported
            with InstanceLock(args.models_dir / ".import.lock"):
                if args.model_command == "import":
                    result = import_model(args.path, name=args.name, lab_path=args.lab,
                                          sample_rate=args.sample_rate, accel_range=args.accel_range,
                                          gyro_range=args.gyro_range, directory=args.models_dir,
                                          replace=args.replace, allow_window_v4=args.allow_window_v4)
                else:
                    result = load_imported(args.name, args.models_dir).manifest
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        logger = make_logger(args.state_dir)
        if args.command == "test-key":
            async def test_key():
                keyboard = None if args.dry_run else MacKeyboard(config.key_hold_s)
                if keyboard and not keyboard.trusted:
                    raise PermissionError("缺少辅助功能权限；先执行 ./ringphone doctor")
                print(f"{args.delay:g} 秒后{'模拟' if args.dry_run else '发送'}一次 {args.key}；请切到目标配置界面。", flush=True)
                await asyncio.sleep(args.delay)
                if keyboard:
                    await keyboard.press(args.key)
                print("仅模拟，未发送按键。" if args.dry_run else "已投递 Mac 按键；请观察 iPhone 是否上滑。")
            with InstanceLock(args.state_dir / "bridge.lock"):
                asyncio.run(test_key())
            return 0
        load_sdk()
        from bleak import BleakClient  # Fail early rather than retry a missing dependency forever.
        del BleakClient
        with InstanceLock(args.state_dir / "bridge.lock"):
            asyncio.run(async_command(args, config, logger))
        return 0
    except KeyboardInterrupt:
        print("已取消。", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"错误：{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
