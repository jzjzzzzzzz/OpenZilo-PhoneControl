"""Capture labeled ring-imu/v1 sessions to a private local dataset."""
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
import time

from .ble import connect_profile
from .storage import read_profile, write_json


async def record(args, config, logger, *, connector=connect_profile):
    import json
    profile = read_profile(args.state_dir / 'ring-profile.json')
    link, _ = await connector(profile, config, logger)
    name = f'{datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")}-{args.label}'
    args.output_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = args.output_dir / f'{name}.jsonl'
    report = {'schema': 'phonecontrol/capture-v1', 'label': args.label, 'records': 0, 'complete': False}
    try:
        sensor = await link.start_imu()
        report['sensor'] = asdict(sensor)
        start = time.monotonic()
        link.activate()
        deadline = start + args.duration
        previous, sequence = None, 0
        with path.open('x') as stream:
            path.chmod(0o600)
            while time.monotonic() < deadline:
                if link.disconnected.is_set():
                    raise ConnectionError('戒指已断开')
                try:
                    batch, received = await asyncio.wait_for(link.imu.get(), min(3, deadline - time.monotonic()))
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
        report['complete'] = True
    finally:
        await link.close()
        write_json(args.output_dir / f'{name}.json', report)
    print(f'IMU 已导出：{path}（{report["records"]} 帧）')
    return report
