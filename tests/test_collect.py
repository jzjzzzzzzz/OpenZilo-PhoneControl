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

    async def test_failed_capture_keeps_partial_and_reports_primary_error(self):
        import json
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
                    self.disconnected.set()
                async def close(self):
                    self.closed = True
                    raise RuntimeError('cleanup failed')
            link = Link()
            async def connector(*args):
                return link, None
            args = SimpleNamespace(state_dir=folder, output_dir=folder / 'captures', label='up', duration=.03)
            with self.assertRaises(ConnectionError):
                await record(args, Config(), None, connector=connector)
            self.assertTrue(link.closed)
            self.assertEqual(list(args.output_dir.glob('*.jsonl')), [])
            self.assertEqual(len(list(args.output_dir.glob('*.partial'))), 1)
            report = json.loads(next(args.output_dir.glob('*.json')).read_text())
            self.assertEqual(report['status'], 'failed')
            self.assertIn('ConnectionError', report['error'])
            self.assertIn('cleanup failed', report['cleanup_error'])

    async def test_invalid_duration_never_connects(self):
        for duration in [0, -1, float('nan'), float('inf'), True]:
            async def connector(*args):
                self.fail('invalid capture must not connect')
            args = SimpleNamespace(duration=duration, label='idle')
            with self.assertRaises(ValueError):
                await record(args, Config(), None, connector=connector)

    async def test_cancelled_capture_closes_and_reports(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            write_json(folder / 'ring-profile.json', {'schema_version': 1, 'address': 'TEST', 'cpuid': 'TEST'})
            ready = asyncio.Event()
            class Link:
                disconnected = asyncio.Event()
                imu = asyncio.Queue()
                closed = False
                async def start_imu(self):
                    return Sensor()
                def activate(self):
                    ready.set()
                async def close(self):
                    self.closed = True
            link = Link()
            async def connector(*args):
                return link, None
            args = SimpleNamespace(state_dir=folder, output_dir=folder / 'captures', label='down', duration=10)
            task = asyncio.create_task(record(args, Config(), None, connector=connector))
            await asyncio.wait_for(ready.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(link.closed)
            self.assertEqual(list(args.output_dir.glob('*.jsonl')), [])
            report = json.loads(next(args.output_dir.glob('*.json')).read_text())
            self.assertEqual(report['status'], 'cancelled')
