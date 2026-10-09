"""Capture labeled ring-imu/v1 sessions to a private local dataset."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import time
import math
import os

from .ble import connect_profile
from .storage import read_profile, write_json


async def record(args, config, logger, *, connector=connect_profile):
    import json
    if isinstance(args.duration, bool) or not math.isfinite(args.duration) or args.duration <= 0:
        raise ValueError('采集时长必须为正有限数值')
    if args.label not in {'idle', 'up', 'down'}:
        raise ValueError('无效的手势标签')
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    profile = read_profile(args.state_dir / 'ring-profile.json')
    link, _ = await connector(profile, config, logger)
    name = f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}-{args.label}'
    path = args.output_dir / f'{name}.jsonl'
    partial = path.with_suffix('.partial')
    failure = None
    report = {'schema': 'phonecontrol/capture-v1', 'label': args.label, 'records': 0, 'complete': False}
    try:
        sensor = await link.start_imu()
        report['sensor'] = asdict(sensor)
        if not math.isfinite(sensor.sample_rate_hz) or sensor.sample_rate_hz <= 0:
            raise ValueError('无效的 IMU 采样率')
        start = time.monotonic()
        link.activate()
        deadline = start + args.duration
        previous, sequence = None, 0
        with os.fdopen(os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
            while time.monotonic() < deadline:
                if link.disconnected.is_set():
                    raise ConnectionError('戒指已断开')
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    batch, received = await asyncio.wait_for(link.imu.get(), min(3, remaining))
                except TimeoutError:
                    if time.monotonic() >= deadline:
                        break
                    raise RuntimeError('IMU 数据中断')
                if received < start:
                    continue
                for index, sample in enumerate(batch.samples):
                    device_sequence = (batch.sequence_start + index) & 0xFFFFFFFF
                    if previous is not None:
                        delta = (device_sequence - previous) & 0xFFFFFFFF
                        if not 0 < delta < 0x80000000:
                            continue
                        sequence += delta
                    previous = device_sequence
                    row = {'schema': 'ring-imu/v1', 'session_id': name, 'label': args.label,
                           'sample_rate_hz': sensor.sample_rate_hz, 'sequence': sequence,
                           'timestamp_ms': sample.timestamp_ms,
                           'accel_raw': [sample.accel_x, sample.accel_y, sample.accel_z],
                           'gyro_raw': [sample.gyro_x, sample.gyro_y, sample.gyro_z],
                           'accel_range_g': sensor.accel_range_g, 'gyro_range_dps': sensor.gyro_range_dps}
                    stream.write(json.dumps(row) + '\n')
                    report['records'] += 1
        if report['records'] < max(2, round(args.duration * sensor.sample_rate_hz * .5)):
            raise RuntimeError('IMU 帧数不足；检查戒指手势模式')
        partial.rename(path)
        report['complete'] = True
        report['status'] = 'completed'
    except BaseException as exc:
        failure = exc
        report['status'] = 'cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed'
        report['error'] = f'{type(exc).__name__}: {exc}'
        raise
    finally:
        try:
            await link.close()
        except BaseException as exc:
            report['cleanup_error'] = f'{type(exc).__name__}: {exc}'
            if failure is None:
                raise
        finally:
            write_json(args.output_dir / f'{name}.json', report)
    print(f'IMU 已导出：{path}（{report["records"]} 帧）')
    return report
