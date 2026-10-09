"""Import the bundled example and export per-window recognition results offline."""
from pathlib import Path
import argparse
import json
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    from ring_iphone.models import import_model, load_imported
    data = json.loads((ROOT / 'demo/imu-windows.json').read_text())
    with tempfile.TemporaryDirectory() as temporary:
        destination = Path(temporary)
        import_model(ROOT / 'demo/imu-baseline.npz', name='demo', lab_path=args.lab,
                     sample_rate=100, accel_range=16, gyro_range=2000, directory=destination)
        artifact = load_imported('demo', destination)
        predictions = []
        for clip in data['clips']:
            normalized = [[value / 32768 for value in frame] for frame in clip['samples']]
            probabilities = artifact.predict(normalized)
            predicted = max(probabilities, key=probabilities.get)
            predictions.append({'input_label': clip['label'], 'frames': len(normalized),
                                'predicted': predicted, 'probabilities': probabilities,
                                'mapped_action': {'up': 'next', 'down': 'previous', 'idle': None}[predicted]})
    report = {'schema': 'phonecontrol/demo-inference-v1', 'data_source': 'synthetic_imu_only',
              'model_import_verified': True, 'phone_actions_sent': 0, 'predictions': predictions}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps(report, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
