from contextlib import redirect_stdout
import io
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from ring_iphone.ble import RingLink
from ring_iphone.cli import pair
from ring_iphone.config import Config
from ring_iphone.sdk import load_sdk
from ring_iphone.storage import read_profile, write_json
from fakes import FakeBleak, candidate, quiet_logger


class PairingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "state"
        self.path = self.state / "ring-profile.json"
        self.args = SimpleNamespace(state_dir=self.state, profile=None, address=None,
                                    cpuid=None, replace=False)
        self.scanner = AsyncMock(return_value=[candidate()])
        self.links = []
        def factory(*args, **kwargs):
            link = RingLink(*args, **kwargs, client_factory=FakeBleak)
            self.links.append(link)
            return link
        self.patches = [patch("ring_iphone.cli.scan", self.scanner),
                        patch("ring_iphone.cli.RingLink", factory),
                        patch("ring_iphone.ble.CONNECTION_STABILIZATION_S", 0)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()

    async def run_pair(self):
        with redirect_stdout(io.StringIO()):
            await pair(self.args, Config(), quiet_logger())

    async def test_new_pair_is_verified_saved_and_closed(self):
        await self.run_pair()
        self.assertEqual(read_profile(self.path)["cpuid"], "TEST-CPU")
        self.assertTrue(self.links[0].client.closed)
        self.assertFalse(self.links[0].accept_events)

    async def test_ambiguous_scan_requires_explicit_selection(self):
        self.scanner.return_value = [candidate("ONE"), candidate("TWO")]
        with self.assertRaisesRegex(ValueError, "多枚"):
            await self.run_pair()
        self.assertEqual(self.links, [])
        self.assertFalse(self.path.exists())

    async def test_explicit_address_selects_exact_candidate(self):
        self.scanner.return_value = [candidate("ONE"), candidate("TWO")]
        self.args.address = "two"
        await self.run_pair()
        self.assertEqual(read_profile(self.path)["address"], "TWO")
        self.assertEqual(len(self.links), 1)

    async def test_existing_binding_never_silently_changes_identity(self):
        original = {"schema_version": 1, "address": "OLD", "cpuid": "OLD-CPU"}
        write_json(self.path, original)
        with self.assertRaisesRegex(ValueError, "未更改绑定"):
            await self.run_pair()
        self.assertEqual(read_profile(self.path), original)
        self.assertTrue(self.links[0].client.closed)

    async def test_replace_is_explicit_and_updates_profile(self):
        write_json(self.path, {"schema_version": 1, "address": "OLD", "cpuid": "OLD-CPU"})
        self.args.replace = True
        await self.run_pair()
        self.assertEqual(read_profile(self.path)["cpuid"], "TEST-CPU")

    async def test_import_desktop_is_verified_and_old_file_untouched(self):
        root = Path(self.tmp.name)
        desktop = root / "ring-desktop/state/ring-profile.json"
        write_json(desktop, {"schema_version": 1, "address": "OLD-UUID", "cpuid": "TEST-CPU"})
        before = desktop.read_bytes()
        self.args.profile = desktop
        with patch("ring_iphone.cli.ROOT", root / "app"):
            await self.run_pair()
        self.assertEqual(desktop.read_bytes(), before)
        self.assertEqual(read_profile(self.path)["address"], "TEST-UUID")

    async def test_no_devices_leaves_profile_absent(self):
        self.scanner.return_value = []
        with self.assertRaisesRegex(ValueError, "未发现"):
            await self.run_pair()
        self.assertFalse(self.path.exists())
