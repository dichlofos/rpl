import json
import logging
import os
import platform
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

EVENT_LOCK = threading.Lock()


def state_directory():
    override = os.getenv("LRPL_STATE_DIR")
    if override:
        return Path(override)
    system = platform.system()
    if system == "Windows":
        root = Path(os.getenv("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
        return root / "LRPL"
    if system == "Darwin":
        return Path.home() / "Library" / "Logs" / "LRPL"
    return Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state") / "lrpl"


def setup_logging():
    directory = state_directory()
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("lrpl")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        handler = RotatingFileHandler(
            directory / "lrpl.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logger.addHandler(handler)
    return logger, directory / "events.jsonl"


def product_event(path, event, **fields):
    record = {
        "at": datetime.now(timezone.utc).isoformat(),
        "event": event,
        **fields,
    }
    with EVENT_LOCK, Path(path).open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
