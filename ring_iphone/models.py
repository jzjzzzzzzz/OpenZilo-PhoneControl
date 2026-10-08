"""Import local NPZ weights and use a separately obtained Motion Lab runtime."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re
import shutil
import sys
import tempfile
import zipfile

from .config import ROOT
from .storage import utc_now, write_json

TRAINING_REPOSITORY = "https://github.com/jzjzzzzzzz/combodied-motion-lab"
MAX_ARCHIVE_BYTES = 128 * 1024 * 1024


def model_paths(name: str, directory: Path):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}", name):
        raise ValueError("模型名称只允许字母、数字、下划线与连字符，长度不超过 64")
    return directory / f"{name}.npz", directory / f"{name}.json"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_lab(path: Path):
    """Explicitly loads user-selected Python source, isolated from global ringml."""
    package = path.expanduser().resolve() / "ml" / "ringml"
    entry = package / "__init__.py"
    if not entry.is_file():
        raise ValueError("--lab 必须指向 combodied-motion-lab 仓库根目录")
    name = "_phonecontrol_lab_" + hashlib.sha256(str(package).encode()).hexdigest()[:16]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, entry, submodule_search_locations=[str(package)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except BaseException:
            for key in list(sys.modules):
                if key == name or key.startswith(name + "."):
                    del sys.modules[key]
            raise
    return sys.modules[name]


def check_probabilities(values, class_count: int):
    import numpy as np
    probabilities = np.asarray(values, dtype=float)
    if (probabilities.shape != (1, class_count) or not np.isfinite(probabilities).all()
            or (probabilities < 0).any() or (probabilities > 1).any()
            or not np.isclose(probabilities.sum(), 1.0, atol=1e-5)):
        raise ValueError("模型输出不是有效的 [1, classes] 概率分布")
    return probabilities[0]


def validate_model(path: Path, lab, *, allow_window_v4=False):
    import numpy as np
    if path.suffix.lower() != ".npz" or path.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("只接受不超过 128 MiB 的 NPZ 导出")
    with zipfile.ZipFile(path) as archive:
        entries = archive.infolist()
        if len(entries) > 128 or sum(e.file_size for e in entries) > MAX_ARCHIVE_BYTES:
            raise ValueError("模型展开体积或条目数量超出限制")
        if len({e.filename for e in entries}) != len(entries):
            raise ValueError("模型包含重复 NPZ 条目")
    with np.load(path, allow_pickle=False) as arrays:
        for name in arrays.files:
            array = arrays[name]
            if array.dtype.hasobject:
                raise ValueError("不接受 pickle/object 数组")
            if np.issubdtype(array.dtype, np.number) and not np.isfinite(array).all():
                raise ValueError(f"模型数组 {name} 包含非有限值")
    model = lab.RNNGestureClassifier.load(path)
    labels = model.classes.tolist()
    if len(labels) < 2 or len(labels) > 64 or len(set(labels)) != len(labels) or "idle" not in labels:
        raise ValueError("RNN 类别必须唯一，包含 idle，且总数为 2～64")
    if model.feature_version not in {"v1", "v2", "v3", "v4"}:
        raise ValueError("不支持该特征版本")
    if model.feature_version == "v4" and not allow_window_v4:
        raise ValueError("v4 会使用窗口内 22 通道预处理；确认与训练/验证流程匹配后添加 --allow-window-v4")
    if not 2 <= model.target_steps <= 512:
        raise ValueError("target_steps 必须在 2～512 之间")
    if not 0.1 <= model.window_seconds <= 10 or not 0 < model.stride_seconds <= model.window_seconds:
        raise ValueError("模型窗口/步长无效")
    if not (model.feature_std > 0).all():
        raise ValueError("feature_std 必须全部大于零")
    method = model.metadata.get("resample_method")
    if method not in {"anti_alias_bin_average_v1", "linear_interpolation_v1"}:
        raise ValueError("模型必须声明支持的 resample_method；请参考 Motion Lab 导出文档")
    channels = 22 if model.feature_version == "v4" else 6
    check_probabilities(model.predict_proba(np.zeros((1, model.target_steps, channels))), len(labels))
    return model


def import_model(source: Path, *, name: str, lab_path: Path, sample_rate: float,
                 accel_range: float, gyro_range: float, directory=ROOT / "models",
                 replace=False, allow_window_v4=False):
    weights, manifest_path = model_paths(name, directory)
    if (weights.exists() or manifest_path.exists()) and not replace:
        raise ValueError("该模型名称已存在；如需覆盖请添加 --replace")
    for value, low, high in [(sample_rate, 10, 400), (accel_range, 1, 100), (gyro_range, 1, 10000)]:
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError("采样率或传感器量程无效")
    source = source.expanduser().resolve()
    if source.suffix.lower() != ".npz":
        raise ValueError("只接受 .npz 模型导出")
    if source.stat().st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("模型文件超出 128 MiB")
    lab_path = lab_path.expanduser().resolve()
    lab = load_lab(lab_path)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(dir=directory, prefix=".import-") as tmp:
        staged = Path(tmp) / "weights.npz"
        shutil.copyfile(source, staged)
        staged.chmod(0o600)
        model = validate_model(staged, lab, allow_window_v4=allow_window_v4)
        manifest = {
            "schema": "openzilo-phonecontrol/model-v1", "name": name,
            "sha256": sha256(staged), "imported_at": utc_now(),
            "training_repository": TRAINING_REPOSITORY, "lab_path": str(lab_path),
            "runtime_sha256": {file: sha256(lab_path / "ml/ringml" / file)
                               for file in ("rnn.py", "features.py", "data.py")},
            "classes": model.classes.tolist(), "feature_version": model.feature_version,
            "target_steps": model.target_steps, "window_seconds": model.window_seconds,
            "stride_seconds": model.stride_seconds, "resample_method": model.metadata["resample_method"],
            "sample_rate_hz": sample_rate, "accel_range_g": accel_range, "gyro_range_dps": gyro_range,
            "allow_window_v4": bool(allow_window_v4),
        }
        staged.replace(weights)
        # An interrupted two-file update fails closed via SHA validation on the next load.
        write_json(manifest_path, manifest)
    return manifest


@dataclass
class ImportedModel:
    manifest: dict
    model: object
    lab: object

    def check_sensor(self, info):
        for field in ("sample_rate_hz", "accel_range_g", "gyro_range_dps"):
            if not math.isclose(float(getattr(info, field)), self.manifest[field], rel_tol=1e-5):
                raise ValueError(f"实时 {field} 与导入契约不一致；重新核对训练数据，而非静默缩放")

    def predict(self, normalized):
        import numpy as np
        values = np.asarray(normalized, dtype=np.float64)
        expected = max(2, round(self.model.window_seconds * self.manifest["sample_rate_hz"]))
        if values.shape != (expected, 6) or not np.isfinite(values).all():
            raise ValueError(f"需要有限的 [{expected}, 6] 归一化窗口")
        if self.model.feature_version == "v4":
            features = importlib.import_module(self.lab.__name__ + ".features")
            feature_config = features.FeatureConfig(
                sample_rate_hz=self.manifest["sample_rate_hz"],
                accel_range_g=self.manifest["accel_range_g"],
                gyro_range_dps=self.manifest["gyro_range_dps"],
            )
            values = features.euler_displacement_features(values, feature_config)
        values = self.lab.resample_window(
            values, self.model.target_steps,
            anti_alias=self.manifest["resample_method"] == "anti_alias_bin_average_v1",
        )
        probabilities = check_probabilities(self.model.predict_proba(values[None]), len(self.model.classes))
        return dict(zip(self.model.classes.tolist(), probabilities.tolist()))


def load_imported(name: str, directory=ROOT / "models") -> ImportedModel:
    weights, manifest_path = model_paths(name, directory)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema") != "openzilo-phonecontrol/model-v1" or manifest.get("name") != name:
        raise ValueError("模型清单格式无效")
    if sha256(weights) != manifest["sha256"]:
        raise ValueError("模型 SHA-256 不匹配，请重新导入")
    lab_path = Path(manifest["lab_path"])
    for file in ("rnn.py", "features.py", "data.py"):
        if sha256(lab_path / "ml/ringml" / file) != manifest["runtime_sha256"][file]:
            raise ValueError("Motion Lab 运行时代码已变化，请重新导入并验证模型")
    lab = load_lab(lab_path)
    model = validate_model(weights, lab, allow_window_v4=manifest["allow_window_v4"])
    for field, expected in {
        "classes": model.classes.tolist(), "feature_version": model.feature_version,
        "target_steps": model.target_steps, "window_seconds": model.window_seconds,
        "stride_seconds": model.stride_seconds,
        "resample_method": model.metadata["resample_method"],
    }.items():
        if manifest.get(field) != expected:
            raise ValueError(f"模型清单 {field} 与权重不匹配，请重新导入")
    for field, low, high in (("sample_rate_hz", 10, 400), ("accel_range_g", 1, 100),
                             ("gyro_range_dps", 1, 10000)):
        value = manifest.get(field)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"模型清单 {field} 无效")
    return ImportedModel(manifest, model, lab)


class MotionStream:
    """Bounded windows + continuity checks + idle re-arm; no weights embedded."""
    def __init__(self, artifact: ImportedModel, config):
        self.artifact = artifact
        self.config = config
        self.size = max(2, round(artifact.model.window_seconds * artifact.manifest["sample_rate_hz"]))
        self.stride = max(1, round(artifact.model.stride_seconds * artifact.manifest["sample_rate_hz"]))
        self.generation = 0
        self.reset()

    def reset(self):
        self.generation += 1
        self.rows = deque(maxlen=self.size)
        self.expected_sequence = None
        self.timestamp = None
        self.since_prediction = 0
        self.armed = False
        self.candidate = None
        self.confirmations = 0

    def prepare(self, batch):
        if self.expected_sequence is not None and batch.sequence_start != self.expected_sequence:
            self.reset()
        self.expected_sequence = (batch.sequence_start + batch.frame_count) & 0xFFFFFFFF
        for sample in batch.samples:
            if self.timestamp is not None:
                delta = (sample.timestamp_ms - self.timestamp) & 0xFFFFFFFF
                if not 0 < delta <= 4 * 1000 / self.artifact.manifest["sample_rate_hz"]:
                    self.reset()
                    return None
            self.timestamp = sample.timestamp_ms
            self.rows.append([getattr(sample, field) / 32768.0 for field in
                              ("accel_x", "accel_y", "accel_z", "gyro_x", "gyro_y", "gyro_z")])
            self.since_prediction += 1
        if len(self.rows) < self.size or self.since_prediction < self.stride:
            return None
        self.since_prediction = 0
        return list(self.rows)

    def observe(self, probabilities: dict) -> str | None:
        label = max(probabilities, key=probabilities.get)
        scores = sorted(probabilities.values(), reverse=True)
        if label == "idle" and scores[0] >= self.config.rnn_idle_confidence:
            self.armed = True
            self.candidate = None
            self.confirmations = 0
            return None
        if (not self.armed or label not in self.config.mapping
                or scores[0] < self.config.rnn_confidence
                or scores[0] - scores[1] < self.config.rnn_margin):
            self.candidate = None
            self.confirmations = 0
            return None
        self.confirmations = self.confirmations + 1 if label == self.candidate else 1
        self.candidate = label
        if self.confirmations < self.config.rnn_confirmations:
            return None
        self.armed = False
        self.candidate = None
        self.confirmations = 0
        return label
