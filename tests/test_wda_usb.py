import importlib.util
from pathlib import Path
import plistlib
import struct
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('wda_usb', ROOT / 'scripts/wda_usb.py')
wda = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wda)


class Stream:
    def __init__(self, payload, *, version=1):
        body = plistlib.dumps(payload)
        self.data = struct.pack('<IIII', len(body) + 16, version, 8, 1) + body
        self.closed = False
        self.sent = None
    def settimeout(self, timeout):
        pass
    def connect(self, path):
        pass
    def sendall(self, data):
        self.sent = data
    def recv(self, size):
        chunk, self.data = self.data[:min(size, 7)], self.data[min(size, 7):]
        return chunk
    def close(self):
        self.closed = True


class TransportTests(unittest.TestCase):
    def test_fragmented_plist_response(self):
        stream = Stream({'DeviceList': []})
        actual, result = wda.mux_request({'MessageType': 'ListDevices'}, socket_factory=lambda *a: stream)
        self.assertIs(actual, stream)
        self.assertEqual(result, {'DeviceList': []})
        self.assertEqual(plistlib.loads(stream.sent[16:])['MessageType'], 'ListDevices')

    def test_invalid_protocol_closes_connection(self):
        stream = Stream({'DeviceList': []}, version=2)
        with self.assertRaises(ValueError):
            wda.mux_request({}, socket_factory=lambda *a: stream)
        self.assertTrue(stream.closed)

    def test_refused_device_port_is_not_retried(self):
        stream = Stream({})
        with patch.object(wda, 'mux_request', return_value=(stream, {'Number': 3})) as request:
            with self.assertRaises(ConnectionError):
                wda.connect_device(1)
        self.assertEqual(request.call_count, 1)
        self.assertTrue(stream.closed)

    def test_network_devices_are_not_selected(self):
        stream = Stream({})
        devices = [{'DeviceID': 1, 'Properties': {'ConnectionType': 'Network'}},
                   {'DeviceID': 2, 'Properties': {'ConnectionType': 'USB'}}]
        with patch.object(wda, 'mux_request', return_value=(stream, {'DeviceList': devices})):
            self.assertEqual(wda.choose_device(), 2)
        self.assertTrue(stream.closed)

    def test_multiple_usb_devices_fail_closed(self):
        stream = Stream({})
        devices = [{'DeviceID': i, 'Properties': {'ConnectionType': 'USB'}} for i in (1, 2)]
        with patch.object(wda, 'mux_request', return_value=(stream, {'DeviceList': devices})):
            with self.assertRaises(RuntimeError):
                wda.choose_device()
