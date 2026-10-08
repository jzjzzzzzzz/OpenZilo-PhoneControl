# Public release boundary

This repository publishes the PhoneControl application, not its surrounding development workspace or private device materials.

## Included

- Application source and command-line launchers.
- Public dependency declarations with a reviewed OpenZilo SDK commit.
- Formal English and Simplified Chinese READMEs and operational documentation.
- Example configurations and a small synthetic event replay.
- Automated tests that generate temporary synthetic model parameters.
- CI configuration, repository metadata and the release audit script.

## Excluded

- Actual RNN weights, model manifests and training checkpoints.
- Training datasets, raw IMU/voice captures and derived private results.
- Device bindings, CPUIDs, serial numbers, CoreBluetooth addresses and runtime logs.
- Private SDKs, firmware, hardware design files and historical workspaces.
- Virtual environments, credentials, build outputs and Git history from other projects.

The only published file under `models/` is its README. Training remains in [ComBodied Motion Lab](https://github.com/jzjzzzzzzz/combodied-motion-lab); local import is documented in [Model integration](docs/models.md).

## Review and export

The exact allowlist lives in `scripts/check_release.py`. The audit verifies UTF-8 text, file-size bounds, common credential/device-address patterns and absence of symlinks. It is a guardrail in addition to human review, not a universal secret detector.

After reviewing intentional source changes:

```bash
.venv/bin/python scripts/check_release.py --write-manifest
.venv/bin/python scripts/check_release.py
.venv/bin/python scripts/check_release.py --export /path/to/new-clean-export
```

`release-files.sha256` covers every allowlisted file except itself. Exports create a **new** directory and do not copy runtime state, model artifacts or an existing `.git` directory.

Before a commit, stage only reviewed files. Then run:

```bash
.venv/bin/python scripts/check_release.py --tracked
```

This additionally rejects unexpected Git-tracked paths, nonregular file modes, missing files and differences between the audited working tree and staged content. `.gitignore` alone cannot protect a force-added file; CI performs the tracked-file audit as well. The audit covers the current snapshot, not previous Git history.

## Repository metadata

The intended repository is `jzjzzzzzzz/OpenZilo-PhoneControl`, public, with Topics including `combodied-ai` and `openzilo`. Desired metadata is recorded in `.github/repository.json`; GitHub Topics are configured separately in repository settings.

No model assets are attached to GitHub releases. A project-wide application license has not yet been selected; public visibility does not replace license selection.
