# Security and privacy

## Local execution boundaries

The application connects to a physically verified ring and can emit macOS switch keys only when explicitly enabled. It does not inspect the phone's foreground app and cannot confirm delivery to an iPhone recipe. Pause the bridge when leaving the intended app.

Device profiles, logs, status files and imported model manifests remain local. The application does not download recordings or upload sensor data. Runtime network use is limited to the local BLE link; installing dependencies uses public package/repository services, and Apple's cross-device transport is managed by the operating system.

## Imported artifacts

NPZ imports disable pickle, enforce archive bounds, check numeric values and smoke-test the upstream inference interface. Checksums detect accidental or external changes but do not establish provenance. `--lab` explicitly loads executable Python from a user-selected Motion Lab checkout. Use a source checkout and model you have reviewed; neither the NPZ validator nor the manifest is an execution sandbox.

## Reporting

Use the repository's **Security → Report a vulnerability** channel when enabled. Include a minimal synthetic reproduction, affected revision and expected/observed behavior. If the private reporting option is unavailable, open a nonsensitive issue asking the maintainer to arrange private contact rather than attaching exploit details or private data.

Do not include credentials, device identities, model weights, recordings or unredacted local logs in public reports. General defects can be filed as ordinary redacted GitHub issues.

## Supported baseline

Security fixes target the current `main` branch. No long-term support window or response-time guarantee has been established.
