## Summary

Describe the change and its scope.

## Verification

- [ ] Automated tests pass; model tests use synthetic temporary weights.
- [ ] Manual hardware results are distinguished from simulated tests.
- [ ] No model weights, captures, device identifiers, logs or local paths are included.
- [ ] `release-files.sha256` has been regenerated after review.
- [ ] `python scripts/check_release.py --tracked` passes after staging.
