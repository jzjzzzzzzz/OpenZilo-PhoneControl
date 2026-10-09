import base64
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ring_iphone.cli import main
from ring_iphone.wda import BUNDLE, HTTP, NoRedirect, Phone, WDAError, WDAOutcomeUnknown
from ring_iphone.wda import WDAOutput
from ring_iphone.config import Config


class FakeAPI:
    def __init__(self):
        self.bundle = BUNDLE
        self.sid = 'existing-session'
        self.calls = []
        self.quality = 3

    def request(self, method, path, body=None):
        self.calls.append((method, path, body))
        value = None
        if path == '/status':
            value = {'ready': True, 'build': {}}
        elif path.endswith('/wda/activeAppInfo'):
            value = {'bundleId': self.bundle}
        elif path == '/session':
            self.sid = 'new-session'
            value = {'sessionId': self.sid}
        elif path.endswith('/window/size'):
            value = {'width': 390, 'height': 844}
        elif path.endswith('/appium/settings'):
            if method == 'POST':
                self.quality = body['settings']['screenshotQuality']
            value = {'screenshotQuality': self.quality}
        elif path.endswith('/screenshot'):
            value = base64.b64encode(b'\x89PNG\r\n\x1a\nFAKE').decode()
        elif path.endswith('/source'):
            value = '<Application/>'
        return {'value': value, 'sessionId': self.sid}


class PhoneTests(unittest.TestCase):
    def phone(self):
        api = FakeAPI()
        return Phone(api=api, sleep=lambda duration: None).attach(), api

    def test_up_and_down_are_single_native_swipes(self):
        phone, api = self.phone()
        phone.swipe('up')
        phone.swipe('down')
        swipes = [body for method, path, body in api.calls if path.endswith('/wda/swipe')]
        self.assertEqual(swipes, [{'direction': 'up'}, {'direction': 'down'}])

    def test_wrong_app_never_creates_session_or_mutates(self):
        api = FakeAPI()
        api.bundle = 'other.app'
        with self.assertRaises(WDAError):
            Phone(api=api).attach()
        self.assertTrue(all(method == 'GET' for method, _, _ in api.calls))

    def test_app_change_before_swipe_prevents_touch(self):
        phone, api = self.phone()
        api.bundle = 'other.app'
        with self.assertRaises(WDAError):
            phone.swipe('up')
        self.assertFalse(any(path.endswith('/wda/swipe') for _, path, _ in api.calls))

    def test_new_session_does_not_force_restart_or_terminate_app(self):
        api = FakeAPI()
        api.sid = None
        Phone(api=api).attach()
        body = next(body for _, path, body in api.calls if path == '/session')
        capabilities = body['capabilities']['alwaysMatch']
        self.assertFalse(capabilities['forceAppLaunch'])
        self.assertFalse(capabilities['shouldTerminateApp'])

    def test_existing_session_is_reused(self):
        phone, api = self.phone()
        self.assertEqual(phone.sid, 'existing-session')
        self.assertFalse(any(path == '/session' for _, path, _ in api.calls))

    def test_capture_is_private_and_restores_quality(self):
        phone, api = self.phone()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'capture'
            phone.capture(folder)
            self.assertEqual((folder / 'screen.png').stat().st_mode & 0o777, 0o600)
            self.assertEqual((folder / 'source.xml').read_text(), '<Application/>')
        self.assertEqual(api.quality, 3)

    def test_explicit_dry_run_never_connects_even_for_inspect(self):
        with tempfile.TemporaryDirectory() as tmp, patch('ring_iphone.wda.HTTP') as http, redirect_stdout(io.StringIO()):
            self.assertEqual(main(['phone', 'inspect', '--dry-run', '--state-dir', tmp]), 0)
        http.assert_not_called()

    def test_mutation_failure_is_retained_as_unknown(self):
        phone, api = self.phone()
        original = api.request
        def failing(method, path, body=None):
            if path.endswith('/wda/swipe'):
                raise WDAOutcomeUnknown('lost response')
            return original(method, path, body)
        api.request = failing
        with self.assertRaises(WDAOutcomeUnknown):
            phone.swipe('up')
        self.assertEqual(phone.mutations, [{'operation': 'swipe_up', 'outcome': 'unknown'}])


class HTTPTests(unittest.TestCase):
    def test_post_failure_is_unknown_and_not_retried(self):
        class Opener:
            count = 0
            def open(self, request, timeout):
                self.count += 1
                raise OSError('unavailable')
        opener = Opener()
        with self.assertRaises(WDAOutcomeUnknown):
            HTTP(opener=opener).request('POST', '/session', {})
        self.assertEqual(opener.count, 1)

    def test_redirects_and_invalid_routes_are_rejected(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://example.com'))
        for path in ('https://example.com', '/../../status'):
            with self.assertRaises(ValueError):
                HTTP().request('GET', path)


class AdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_bridge_adapter_prepares_and_swipes_once(self):
        api = FakeAPI()
        output = WDAOutput(Config(output_backend='wda'), api=api, clock=lambda: 0)
        await output.prepare()
        await output.perform('next', guard=lambda: True, valid_until=1)
        self.assertEqual(sum(path.endswith('/wda/swipe') for _, path, _ in api.calls), 1)
        self.assertTrue(output.last_attempt['completed'])

    async def test_expired_adapter_input_never_swipes(self):
        api = FakeAPI()
        output = WDAOutput(Config(output_backend='wda'), api=api, clock=lambda: 2)
        await output.prepare()
        with self.assertRaises(RuntimeError):
            await output.perform('next', guard=lambda: True, valid_until=1)
        self.assertFalse(any(path.endswith('/wda/swipe') for _, path, _ in api.calls))

    async def test_revoked_adapter_input_never_sends(self):
        api = FakeAPI()
        output = WDAOutput(Config(output_backend='wda'), api=api)
        with self.assertRaises(RuntimeError):
            await output.perform('previous', guard=lambda: False)
        self.assertEqual(api.calls, [])
