# Contributing

## Development environment

Use a source checkout with Python 3.11 or newer. The supported installation is editable: the CLI's bundled setup guide, replay fixture and default local state/model directories are resolved relative to the checkout. Standalone wheel distribution is not currently an installation target.

```bash
./setup.sh
.venv/bin/python -m unittest discover -s tests -v
./ringphone replay
```

The default event path requires no model. For optional model tests, obtain the reviewed Motion Lab revision separately and set `COMBODIED_MOTION_LAB_PATH`; see [testing](docs/testing.md). Do not vendor its source or commit exported weights.

## Change boundaries

- Preserve dry-run by default and explicit activation of real keyboard output.
- Keep discrete events and RNN predictions behind the same command gate.
- Preserve physical device verification, bounded queues and fail-closed model contracts.
- Do not add recording downloads, firmware writes or automatic accessibility-setting changes to the control path.
- Keep hardware acceptance separate from unit-test results. Never infer iPhone success from Mac key submission.
- Document changes to preprocessing, labels, sensor assumptions or supported input modes.

## Pull requests

Explain the behavior change, reproduction steps and tests. Include synthetic fixtures rather than device captures. Update the exact publication allowlist for new public files; review and regenerate `release-files.sha256`, stage reviewed changes, and run `scripts/check_release.py --tracked` before submitting.

Report defects through GitHub issues with redacted diagnostics. Follow [SECURITY.md](SECURITY.md) for vulnerabilities. License selection for this application remains a maintainer decision; contributors should resolve contribution terms with the maintainer before substantial external contributions.
