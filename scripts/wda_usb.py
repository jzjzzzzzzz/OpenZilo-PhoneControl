#!/usr/bin/env python3
"""Read-only WDA status probe or loopback-only USB forwarding; no UI actions."""
from __future__ import annotations

import argparse
import http.client
import json
import plistlib
import select
import socket
import socketserver
import struct

USBMUX_SOCKET = "/var/run/usbmuxd"
MAX_PACKET = 1024 * 1024


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.recv(size - len(data))
        if not chunk:
            raise ConnectionError("usbmuxd closed the connection")
        data.extend(chunk)
    return bytes(data)


def mux_request(payload, *, socket_factory=socket.socket):
    stream = socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        stream.settimeout(5)
        stream.connect(USBMUX_SOCKET)
        body = plistlib.dumps({"ProgName": "OpenZilo-PhoneControl", **payload})
        stream.sendall(struct.pack("<IIII", len(body) + 16, 1, 8, 1) + body)
        length, version, kind, tag = struct.unpack("<IIII", read_exact(stream, 16))
        if not 16 <= length <= MAX_PACKET or (version, kind, tag) != (1, 8, 1):
            raise ValueError("Invalid usbmuxd response header")
        response = plistlib.loads(read_exact(stream, length - 16))
        if not isinstance(response, dict):
            raise ValueError("Invalid usbmuxd response body")
        return stream, response
    except BaseException:
        stream.close()
        raise


def choose_device(device_id=None):
    stream, response = mux_request({"MessageType": "ListDevices"})
    stream.close()
    devices = [item for item in response.get("DeviceList", [])
               if item.get("Properties", {}).get("ConnectionType") == "USB"]
    if device_id is not None:
        devices = [item for item in devices if item.get("DeviceID") == device_id]
    if len(devices) != 1:
        raise RuntimeError(f"Expected one USB iPhone/iPad; found {len(devices)}. Connect only the target device.")
    result = devices[0].get("DeviceID")
    if type(result) is not int or result <= 0:
        raise ValueError("Invalid usbmuxd device number")
    return result


def connect_device(device_id, device_port=8100):
    stream, response = mux_request({"MessageType": "Connect", "DeviceID": device_id,
                                    "PortNumber": socket.htons(device_port)})
    code = response.get("Number")
    if type(code) is not int or code != 0:
        stream.close()
        message = "device port refused; keep the WDA test running" if code == 3 else "USB connection failed"
        raise ConnectionError(f"{message} (usbmuxd result {code})")
    return stream


def probe(device_id, device_port=8100):
    class DeviceHTTP(http.client.HTTPConnection):
        def connect(self):
            self.sock = connect_device(device_id, device_port)
    connection = DeviceHTTP("localhost", device_port, timeout=5)
    try:
        connection.request("GET", "/status", headers={"Connection": "close"})
        response = connection.getresponse()
        body = response.read(256 * 1024 + 1)
        if len(body) > 256 * 1024 or response.status != 200:
            raise RuntimeError(f"Unexpected WDA status response: HTTP {response.status}")
        data = json.loads(body)
        value = data.get("value") if isinstance(data, dict) else None
        if not isinstance(value, dict) or not isinstance(value.get("build"), dict):
            raise RuntimeError("Device port did not return a recognized WDA status")
        return {"wda_status": "reachable", "ready": value.get("ready"),
                "usb": True, "iphone_mirroring": False, "phone_actions_sent": 0}
    finally:
        connection.close()


def forward(local_port, device_id, device_port):
    class Handler(socketserver.BaseRequestHandler):
        def handle(self):
            upstream = None
            try:
                upstream = connect_device(device_id, device_port)
                upstream.settimeout(None)
                self.request.settimeout(None)
                sockets = (self.request, upstream)
                while True:
                    readable, _, _ = select.select(sockets, (), (), 60)
                    if not readable:
                        return
                    for source in readable:
                        data = source.recv(65536)
                        if not data:
                            return
                        destination = upstream if source is self.request else self.request
                        destination.sendall(data)
            except (OSError, ValueError, RuntimeError) as exc:
                print(f"USB forward connection stopped: {type(exc).__name__}", flush=True)
            finally:
                if upstream is not None:
                    upstream.close()

    class Server(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True
    with Server(("127.0.0.1", local_port), Handler) as server:
        print(f"USB forwarding: http://127.0.0.1:{local_port} -> device:{device_port}", flush=True)
        print("Keep this terminal and the Xcode WDA test open. Ctrl-C stops forwarding.", flush=True)
        server.serve_forever()


def port_number(value):
    result = int(value)
    if not 1024 <= result <= 65535:
        raise argparse.ArgumentTypeError("Port must be 1024–65535")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device-id", type=int, help="Transient usbmuxd device number; normally auto-selected")
    parser.add_argument("--device-port", type=port_number, default=8100)
    parser.add_argument("--local-port", type=port_number, default=18100)
    parser.add_argument("--probe", action="store_true", help="GET /status only, then exit; do not open a forwarding port")
    args = parser.parse_args(argv)
    try:
        device_id = choose_device(args.device_id)
        print(json.dumps(probe(device_id, args.device_port), indent=2), flush=True)
        if not args.probe:
            forward(args.local_port, device_id, args.device_port)
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"Error: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
