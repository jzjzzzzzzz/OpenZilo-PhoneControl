from functools import lru_cache
import importlib


@lru_cache(maxsize=1)
def load_sdk():
    try:
        module = importlib.import_module("openzilo")
    except ImportError as exc:
        raise RuntimeError("缺少公开 OpenZilo SDK；请执行 ./setup.sh") from exc
    if module.__version__ != "0.5.0":
        raise RuntimeError(f"需要已核对的 OpenZilo SDK 0.5.0，实际为 {module.__version__}")
    return module
