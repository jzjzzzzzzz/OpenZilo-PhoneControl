# Third-party components and references

No upstream source code, model weights, firmware, logos or hardware assets are vendored in this application snapshot.

| Component | Role | Upstream terms / source |
| --- | --- | --- |
| OpenZilo Python SDK | Device framing, parsers and public IMU reporting interface | [MPL-2.0](https://github.com/ziloai/OpenZilo/blob/main/LICENSE), dependency pinned at `e9a861dd5c82a154fba0f1bafb628cd04fbd9555` |
| Bleak | Platform BLE transport | [MIT](https://github.com/hbldh/bleak/blob/develop/LICENSE) |
| NumPy | Optional model arrays and numerical inference | [BSD-3-Clause and bundled notices](https://github.com/numpy/numpy/blob/main/LICENSE.txt) |
| ComBodied Motion Lab | Separately obtained training system and optional inference runtime | [Repository](https://github.com/jzjzzzzzzz/combodied-motion-lab); no project-wide license identified in reviewed revision `839ecc0bc89fd560e29eb6cc1b41f17afe5a51d7`; obtain separately and review its terms |
| Apple Switch Control | Operating-system accessibility mechanism | [Apple platform documentation](https://support.apple.com/en-us/118667) |

Installed distributions may contain additional transitive dependencies and notices. Their terms remain with their respective distributions. Model owners control the terms of their imported weights and datasets.

This project references OpenZilo and ComBodied AI to identify its ecosystem and interoperability target. It is not an official OpenZilo, Apple or Douyin release.

The OpenZilo SDK's MPL-2.0 license does not automatically license this separate application. No project-wide application license has yet been selected for this snapshot.
