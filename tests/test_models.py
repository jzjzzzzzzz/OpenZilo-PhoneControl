"""Synthetic fixtures only: no trained weights or captured sensor data."""
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from ring_iphone.config import Config
from ring_iphone.models import MotionStream, import_model, load_imported, load_lab, model_paths
from ring_iphone.sdk import load_sdk
from fakes import imu_packet

LAB_PATH = os.environ.get("COMBODIED_MOTION_LAB_PATH")


class StreamTests(unittest.TestCase):
    def setUp(self):
        self.artifact = SimpleNamespace(model=SimpleNamespace(window_seconds=0.1, stride_seconds=0.05),
                                        manifest={"sample_rate_hz": 100})
        self.stream = MotionStream(self.artifact, Config(mapping={"up": "next"}))

    def batch(self, **kwargs):
        sdk = load_sdk()
        return sdk.parse_sensor_data_batch(sdk.PacketStream().feed(imu_packet(**kwargs))[0].body)

    def test_raw_normalization(self):
        window = self.stream.prepare(self.batch())
        self.assertEqual(len(window), 10)
        self.assertEqual(window[0], [x / 32768 for x in (100, -200, 300, -400, 500, -600)])

    def test_gap_resets_rearm_and_window(self):
        self.stream.prepare(self.batch())
        self.stream.observe({"idle": 0.99, "up": 0.01})
        generation = self.stream.generation
        self.assertIsNone(self.stream.prepare(self.batch(sequence=20, count=5, start_ms=200)))
        self.assertFalse(self.stream.armed)
        self.assertGreater(self.stream.generation, generation)
        self.assertEqual(len(self.stream.rows), 5)

    def test_timestamp_discontinuity_clears_all(self):
        self.stream.prepare(self.batch())
        self.assertIsNone(self.stream.prepare(self.batch(sequence=10, start_ms=500)))
        self.assertEqual(len(self.stream.rows), 0)

    def test_timestamp_and_sequence_wrap_are_continuous(self):
        self.stream.prepare(self.batch(sequence=0xFFFFFFF6, start_ms=0xFFFFFFA6))
        generation = self.stream.generation
        self.assertIsNotNone(self.stream.prepare(self.batch(sequence=0, start_ms=10)))
        self.assertEqual(self.stream.generation, generation)

    def test_idle_rearm_confirmations_and_no_repeated_gesture(self):
        up = {"idle": 0.01, "up": 0.99}
        self.assertIsNone(self.stream.observe(up))
        self.stream.observe({"idle": 0.99, "up": 0.01})
        self.assertIsNone(self.stream.observe(up))
        self.assertEqual(self.stream.observe(up), "up")
        for _ in range(10):
            self.assertIsNone(self.stream.observe(up))
        self.stream.observe({"idle": 0.99, "up": 0.01})
        self.assertIsNone(self.stream.observe(up))
        self.assertEqual(self.stream.observe(up), "up")

    def test_uncertain_prediction_resets_confirmation(self):
        self.stream.observe({"idle": 0.99, "up": 0.01})
        self.stream.observe({"idle": 0.01, "up": 0.99})
        self.stream.observe({"idle": 0.3, "up": 0.7})
        self.assertIsNone(self.stream.observe({"idle": 0.01, "up": 0.99}))

    def test_invalid_names(self):
        for name in ("../secret", "", "a/b", ".hidden", "x" * 65):
            with self.assertRaises(ValueError):
                model_paths(name, Path("models"))


@unittest.skipUnless(LAB_PATH, "set COMBODIED_MOTION_LAB_PATH for public runtime integration")
class ImportTests(unittest.TestCase):
    def setUp(self):
        import numpy as np
        self.np = np
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.lab_path = Path(LAB_PATH)
        self.lab = load_lab(self.lab_path)
        self.source = self.root / "synthetic.npz"
        self.destination = self.root / "models"
        self.make_model()

    def make_model(self, version="v3", **changes):
        np = self.np
        channels = 22 if version == "v4" else self.lab.rnn.angle_sequence_features(
            np.zeros((1, 10, 6)), version=version).shape[2]
        h = 2
        params = {name: np.zeros(shape) for name, shape in {
            "gru_weight_ih_l0": (3*h, channels), "gru_weight_hh_l0": (3*h, h),
            "gru_bias_ih_l0": (3*h,), "gru_bias_hh_l0": (3*h,),
            "attention_weight": (1, h), "attention_bias": (1,),
            "fc1_weight": (2, 4*h), "fc1_bias": (2,),
            "fc2_weight": (3, 2), "fc2_bias": (3,),
        }.items()}
        args = dict(classes=["idle", "up", "down"], target_steps=10, window_seconds=0.1,
                    stride_seconds=0.05, feature_mean=np.zeros(channels), feature_std=np.ones(channels),
                    parameters=params, metadata={"feature_transform_version": version,
                                                 "resample_method": "anti_alias_bin_average_v1"})
        args.update(changes)
        self.lab.RNNGestureClassifier(**args).save(self.source)

    def do_import(self, **kwargs):
        return import_model(self.source, name="synthetic", lab_path=self.lab_path,
                            sample_rate=100, accel_range=16, gyro_range=2000,
                            directory=self.destination, **kwargs)

    def test_import_load_predict_all_standard_versions(self):
        for version in ("v1", "v2", "v3"):
            self.make_model(version)
            self.do_import(replace=True)
            artifact = load_imported("synthetic", self.destination)
            self.assertEqual(artifact.model.feature_version, version)
            probabilities = artifact.predict(self.np.zeros((10, 6)))
            self.assertEqual(set(probabilities), {"idle", "up", "down"})
            self.assertAlmostEqual(sum(probabilities.values()), 1)
            artifact.check_sensor(SimpleNamespace(sample_rate_hz=100, accel_range_g=16, gyro_range_dps=2000))
            with self.assertRaises(ValueError):
                artifact.check_sensor(SimpleNamespace(sample_rate_hz=50, accel_range_g=16, gyro_range_dps=2000))

    def test_v4_requires_opt_in_and_predicts(self):
        self.make_model("v4")
        with self.assertRaises(ValueError):
            self.do_import()
        self.assertFalse((self.destination / "synthetic.npz").exists())
        self.do_import(allow_window_v4=True)
        artifact = load_imported("synthetic", self.destination)
        self.assertAlmostEqual(sum(artifact.predict(self.np.zeros((10, 6))).values()), 1)

    def test_no_silent_replace(self):
        self.do_import()
        with self.assertRaises(ValueError):
            self.do_import()
        self.do_import(replace=True)

    def test_tampered_weights_rejected(self):
        self.do_import()
        with (self.destination / "synthetic.npz").open("ab") as file:
            file.write(b"tampered")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            load_imported("synthetic", self.destination)

    def test_tampered_runtime_hash_rejected(self):
        manifest = self.do_import()
        manifest["runtime_sha256"]["rnn.py"] = "0" * 64
        (self.destination / "synthetic.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "运行时代码"):
            load_imported("synthetic", self.destination)

    def test_manifest_cannot_override_model_contract(self):
        manifest = self.do_import()
        manifest["classes"] = ["idle", "wave"]
        (self.destination / "synthetic.json").write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, "classes"):
            load_imported("synthetic", self.destination)

    def test_invalid_exports_rejected(self):
        for changes in ({"classes": ["a", "b", "c"]}, {"classes": ["idle", "idle", "up"]},
                        {"target_steps": 999}, {"window_seconds": float("nan")},
                        {"stride_seconds": 2}, {"metadata": {}},
                        {"feature_std": self.np.zeros(18), "feature_mean": self.np.zeros(18)}):
            with self.subTest(changes=list(changes)):
                self.make_model(**changes)
                with self.assertRaises(ValueError):
                    self.do_import()

    def test_pickle_and_nan_rejected(self):
        for value in (self.np.array([object()], dtype=object), self.np.array([float("nan")])):
            self.np.savez(self.source, malicious=value)
            with self.assertRaises(ValueError):
                self.do_import()

    def test_wrong_extension_rejected(self):
        other = self.source.with_suffix(".pt")
        self.source.rename(other)
        self.source = other
        with self.assertRaises(ValueError):
            self.do_import()

    def test_bad_prediction_shape_rejected(self):
        self.do_import()
        with self.assertRaises(ValueError):
            load_imported("synthetic", self.destination).predict(self.np.zeros((9, 6)))

    def test_cli_import_and_inspect(self):
        result = subprocess.run([
            sys.executable, "-m", "ring_iphone", "model", "import", str(self.source),
            "--name", "synthetic", "--lab", str(self.lab_path),
            "--sample-rate", "100", "--accel-range", "16", "--gyro-range", "2000",
            "--models-dir", str(self.destination),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        result = subprocess.run([
            sys.executable, "-m", "ring_iphone", "model", "inspect", "synthetic",
            "--models-dir", str(self.destination),
        ], capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["sha256"], manifest["sha256"])
