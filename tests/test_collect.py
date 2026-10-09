import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time
import unittest

from ring_iphone.collect import record
from ring_iphone.config import Config
from ring_iphone.storage import write_json


@dataclass
class Sensor:
    sample_rate_hz: int = 100
    accel_range_g: int = 16
    gyro_range_dps: int = 2000


class CaptureTests(unittest.IsolatedAsyncioTestCase):
    async def test_capture_exports_raw_samples_and_closes(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_json(folder / 'ring-profile.json', {'schema_version': 1, 'address': 'TEST', 'cpuid': 'TEST'})
            class Link:
                disconnected = asyncio.Event()
                imu = asyncio.Queue()
                closed = False
                async def start_imu(self):
                    return Sensor()
                def activate(self):
                    sample = SimpleNamespace(timestamp_ms=0, accel_x=1, accel_y=2, accel_z=3,
                                             gyro_x=4, gyro_y=5, gyro_z=6)
                    self.imu.put_nowait((SimpleNamespace(sequence_start=0, samples=[sample, sample, sample]), time.monotonic()))
                async def close(self):
                    self.closed = True
            link = Link()
            async def connector(*args):
                return link, None
            args = SimpleNamespace(state_dir=folder, output_dir=folder / 'captures', label='idle', duration=.03)
            result = await record(args, Config(), None, connector=connector)
            self.assertTrue(result['complete'])
            self.assertTrue(link.closed)
            self.assertEqual(result['records'], 3)
            output = next((folder / 'captures').glob('*.jsonl'))
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
