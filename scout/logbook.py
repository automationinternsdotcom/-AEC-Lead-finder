"""Dead-simple per-stage append-only log files. Mirrors GPS scout/logbook.py."""
from __future__ import annotations

import os
from datetime import datetime

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")


def log(stage: str, msg: str) -> None:
    os.makedirs(LOG_DIR, exist_ok=True)
    with open(os.path.join(LOG_DIR, f"{stage}.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.now().isoformat()} {msg}\n")
    print(f"[{stage}] {msg}")
