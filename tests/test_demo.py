import json
import os
from pathlib import Path
import tempfile
import unittest
import asyncio

from ring_iphone.config import ROOT
from ring_iphone.models import import_model, load_imported


@unittest.skipUnless(os.environ.get('COMBODIED_MOTION_LAB_PATH'), 'Motion Lab checkout required')
class DemoTests(unittest.TestCase):
    def test_export_import_and_inference(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            import_model(ROOT / 'demo/imu-baseline.npz', name='demo',
                         lab_path=Path(os.environ['COMBODIED_MOTION_LAB_PATH']),
                         sample_rate=100, accel_range=16, gyro_range=2000, directory=folder)
            model = load_imported('demo', folder)
            self.assertTrue(model.model.metadata['demo_only'])
            for clip in json.loads((ROOT / 'demo/imu-windows.json').read_text())['clips']:
                probabilities = model.predict([[value / 32768 for value in row] for row in clip['samples']])
                self.assertEqual(max(probabilities, key=probabilities.get), clip['label'])
                self.assertAlmostEqual(sum(probabilities.values()), 1)

    def test_imu_predictions_rearm_gate_and_route_to_wda(self):
        from ring_iphone.config import load_config
        from ring_iphone.events import EventGate, RingEvent
        from ring_iphone.models import MotionStream
        from ring_iphone.wda import WDAOutput
        from test_wda import FakeAPI
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            import_model(ROOT / 'demo/imu-baseline.npz', name='demo',
                         lab_path=Path(os.environ['COMBODIED_MOTION_LAB_PATH']),
                         sample_rate=100, accel_range=16, gyro_range=2000, directory=folder)
            artifact = load_imported('demo', folder)
            probabilities = {clip['label']: artifact.predict([[value / 32768 for value in row]
                            for row in clip['samples']]) for clip in json.loads((ROOT / 'demo/imu-windows.json').read_text())['clips']}
            config = load_config(preset='rnn')
            config.output_backend = 'wda'
            config.settle_s = 0
            motion = MotionStream(artifact, config)
            gate = EventGate(config)
            gate.reset(0)
            api = FakeAPI()
            output = WDAOutput(config, api=api, clock=lambda: 0)
            async def exercise():
                await output.prepare()
                for index, label in enumerate(('up', 'down')):
                    motion.observe(probabilities['idle'])
                    self.assertIsNone(motion.observe(probabilities[label]))
                    emitted = motion.observe(probabilities[label])
                    self.assertEqual(emitted, label)
                    now = float(index * 2)
                    decision = gate.handle(RingEvent(label, index, now), now)
                    self.assertEqual(decision.reason, 'accepted')
                    await output.perform(decision.action, guard=lambda: True, valid_until=now + 1)
            asyncio.run(exercise())
            directions = [body['direction'] for _, path, body in api.calls if path.endswith('/wda/swipe')]
            self.assertEqual(directions, ['up', 'down'])
