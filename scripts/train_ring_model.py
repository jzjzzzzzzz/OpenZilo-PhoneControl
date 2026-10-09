"""Run the pinned Motion Lab trainer with the v3 raw-input contract."""
from pathlib import Path
import argparse
import hashlib
import re
import sys
import os

TRAINER_SHA256 = 'c962999ccf4359752c3e4a0a49a682738934c82d85bf06d4a37f7ef42464cac3'


def prepare_source(source):
    replacements = {
        'train_x = torch.tensor(feature_train, dtype=torch.float32)':
            'train_x = torch.tensor(train.windows, dtype=torch.float32)',
        'angle_sequence_features(validation.windows, version=args.feature_version),\n        dtype=torch.float32,':
            'validation.windows,\n        dtype=torch.float32,',
        'angle_sequence_features(test.windows, version=args.feature_version),\n        dtype=torch.float32,':
            'test.windows,\n        dtype=torch.float32,',
    }
    for old, new in replacements.items():
        if source.count(old) != 1:
            raise ValueError('Motion Lab trainer revision does not match the v3 adapter')
        source = source.replace(old, new)
    for name, partition, offset in [('stress_validation_x', 'validation', 190), ('stress_test_x', 'test', 191)]:
        pattern = rf'    {name} = torch.tensor\(\n.*?\n    \)'
        source, count = re.subn(pattern, f'    {name} = torch.tensor(stress_windows({partition}.windows, args.seed + {offset}), dtype=torch.float32)', source, count=1, flags=re.S)
        if count != 1:
            raise ValueError('Stress-input contract not found')
    source = source.replace('"subject_leakage": False,', '"subject_leakage": bool((set(train.subjects) - {""}) & set(validation.subjects) or (set(train.subjects) - {""}) & set(test.subjects)) if np.all(train.subjects != "") else None,')
    return source


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lab', type=Path, required=True)
    args, forwarded = parser.parse_known_args()
    trainer = args.lab.resolve() / 'ml/train_rnn.py'
    data = trainer.read_bytes()
    if hashlib.sha256(data).hexdigest() != TRAINER_SHA256:
        raise ValueError('Use Motion Lab commit 839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7')
    source = prepare_source(data.decode())
    sys.path.insert(0, str(trainer.parent))
    namespace = {'__name__': '_phonecontrol_personal_training', '__file__': str(trainer)}
    exec(compile(source, str(trainer), 'exec'), namespace)
    sys.argv = [str(trainer), *forwarded, '--feature-version', 'v3']
    namespace['main']()


if __name__ == '__main__':
    main()
