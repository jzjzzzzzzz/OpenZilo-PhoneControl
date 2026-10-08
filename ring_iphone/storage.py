from __future__ import annotations

from contextlib import AbstractContextManager
from datetime import datetime, timezone
import fcntl
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import tempfile


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def read_profile(path: Path) -> dict:
    if not path.exists():
        raise ValueError("尚未绑定戒指：先执行 ./ringphone pair 或 ./ringphone pair --profile PATH")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("绑定文件 schema_version 必须为 1")
    for key in ("address", "cpuid"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ValueError(f"绑定文件缺少 {key}")
    return data


class InstanceLock(AbstractContextManager):
    def __init__(self, path: Path):
        self.path = path
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.stream = self.path.open("a+")
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.stream.close()
            raise RuntimeError("已有 ringphone 在扫描、绑定、监控或控制戒指，请先停止它") from None
        self.stream.seek(0)
        self.stream.truncate()
        self.stream.write(str(os.getpid()))
        self.stream.flush()
        return self

    def __exit__(self, *args):
        if self.stream:
            self.stream.close()  # Keep lock inode; unlinking introduces races.


def make_logger(state: Path) -> logging.Logger:
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = state / "bridge.log"
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    logger = logging.getLogger("ringphone")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    file_handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    # Open new rotated logs under a private umask as well.
    handler_format = logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S")
    for handler in (logging.StreamHandler(), file_handler):
        handler.setFormatter(handler_format)
        logger.addHandler(handler)
    return logger
