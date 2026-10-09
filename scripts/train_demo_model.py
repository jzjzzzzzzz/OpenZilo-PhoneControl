"""Build the small synthetic IMU example; personal training lives in Motion Lab."""
from pathlib import Path
import argparse
import copy
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def generate(seed, per_class):
    import numpy as np
    rng = np.random.default_rng(seed)
    t = np.linspace(0, 1, 160)
    records, labels = [], []
    for label in ('down', 'idle', 'up'):
        for _ in range(per_class):
            sign = {'down': -1, 'idle': 0, 'up': 1}[label]
            angle = sign * rng.uniform(.3, .8) * np.sin(np.pi * t)
            accel = np.column_stack((np.zeros(160), np.sin(angle), np.cos(angle)))
            accel += rng.normal(0, .015, accel.shape)
            gyro = np.column_stack((np.degrees(np.gradient(angle, .01)), np.zeros(160), np.zeros(160)))
            gyro += rng.normal(0, 1.5, gyro.shape)
            raw = np.rint(np.column_stack((accel / 16, gyro / 2000)) * 32768).astype(np.int16)
            records.append(raw)
            labels.append(label)
    return np.asarray(records), np.asarray(labels)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, default=ROOT / 'demo')
    parser.add_argument('--epochs', type=int, default=150)
    args = parser.parse_args()
    import numpy as np
    import torch
    from torch import nn
    from ring_iphone.models import load_lab
    lab = load_lab(args.lab)
    torch.set_num_threads(1)
    torch.manual_seed(20261009)
    torch.use_deterministic_algorithms(True)
    classes = ['down', 'idle', 'up']
    def dataset(seed, count):
        raw, labels = generate(seed, count)
        windows = np.stack([lab.resample_window(row.astype(float) / 32768, 40, anti_alias=True) for row in raw])
        features = lab.rnn.angle_sequence_features(windows, version='v3')
        targets = np.asarray([classes.index(label) for label in labels])
        return raw, windows, features, targets
    train = dataset(1001, 30)
    validation = dataset(2002, 10)
    test = dataset(3003, 10)
    mean = train[2].mean((0, 1))
    std = np.maximum(train[2].std((0, 1)), 1e-6)
    class TinyGRU(nn.Module):
        def __init__(self):
            super().__init__()
            self.gru = nn.GRU(len(mean), 8, batch_first=True)
            self.attention = nn.Linear(8, 1)
            self.fc1 = nn.Linear(32, 8)
            self.fc2 = nn.Linear(8, 3)
        def forward(self, values):
            output, _ = self.gru(values)
            attention = torch.softmax(self.attention(output), dim=1)
            pooled = torch.cat(((output * attention).sum(1), output.mean(1), output.max(1).values, output[:, -1]), dim=1)
            return self.fc2(torch.relu(self.fc1(pooled)))
    tensors = [torch.tensor((part[2] - mean) / std, dtype=torch.float32) for part in (train, validation, test)]
    targets = [torch.tensor(part[3]) for part in (train, validation, test)]
    net = TinyGRU()
    optimizer = torch.optim.Adam(net.parameters(), lr=.008)
    best, best_loss = None, float('inf')
    for epoch in range(args.epochs):
        net.train()
        for indices in torch.randperm(len(tensors[0])).split(30):
            optimizer.zero_grad()
            loss = nn.functional.cross_entropy(net(tensors[0][indices]), targets[0][indices])
            loss.backward()
            optimizer.step()
        net.eval()
        with torch.no_grad():
            loss = nn.functional.cross_entropy(net(tensors[1]), targets[1]).item()
        if loss < best_loss:
            best, best_loss = copy.deepcopy(net.state_dict()), loss
    net.load_state_dict(best)
    state = net.state_dict()
    parameters = {key.replace('gru.', 'gru_').replace('.', '_'): value.numpy() for key, value in state.items()}
    sensor = {'sample_rate_hz': 100, 'accel_range_g': 16, 'gyro_range_dps': 2000}
    model = lab.RNNGestureClassifier(classes=classes, target_steps=40, window_seconds=1.6,
        stride_seconds=.1, feature_mean=mean, feature_std=std, parameters=parameters,
        metadata={'feature_transform_version': 'v3', 'resample_method': 'anti_alias_bin_average_v1',
                  'demo_only': True, 'training_data_source': 'synthetic_imu_only', 'sensor_contract': sensor,
                  'seed': 20261009, 'training_repository': 'https://github.com/jzjzzzzzzz/combodied-motion-lab',
                  'usage': 'Train on your own ring recordings before live output.'})
    with torch.no_grad():
        expected = torch.softmax(net(tensors[2]), dim=1).numpy()
    actual = model.predict_proba(test[1])
    np.testing.assert_allclose(actual, expected, atol=3e-5, rtol=3e-5)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    path = args.output_dir / 'imu-baseline.npz'
    model.save(path)
    report = {'schema': 'phonecontrol/demo-model-v1', 'data_source': 'synthetic_imu_only',
              'classes': classes, 'train_windows': 90, 'validation_windows': 30, 'test_windows': 30,
              'epochs': args.epochs, 'parameters': sum(value.numel() for value in state.values()),
              'synthetic_test_accuracy': float(np.mean(actual.argmax(1) == test[3])),
              'torch_numpy_max_error': float(np.max(np.abs(actual - expected))), 'sensor_contract': sensor,
              'model_bytes': path.stat().st_size, 'model_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'training_script': 'scripts/train_demo_model.py', 'python': sys.version.split()[0],
              'numpy': np.__version__, 'torch': torch.__version__, 'seed': 20261009,
              'usage': 'Minimal import/export example. Collect and train your own ring data.'}
    (args.output_dir / 'model-card.json').write_text(json.dumps(report, indent=2) + '\n')
    clips = []
    for index, label in enumerate(classes):
        clips.append({'label': label, 'sample_rate_hz': 100, 'samples': test[0][index * 10].tolist()})
    (args.output_dir / 'imu-windows.json').write_text(json.dumps({'data_source': 'synthetic_imu_only', 'clips': clips}, indent=2) + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
