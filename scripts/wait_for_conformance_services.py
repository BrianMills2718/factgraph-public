#!/usr/bin/env python3
from __future__ import annotations

import socket
import sys
import time

TARGETS = [
    ("PostgreSQL", "127.0.0.1", 55432),
    ("MongoDB", "127.0.0.1", 57017),
    ("TypeDB", "127.0.0.1", 51729),
]


def wait(name: str, host: str, port: int, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=2):
                print(f"{name} TCP endpoint is ready at {host}:{port}")
                return
        except OSError as exc:
            last_error = exc
            time.sleep(1)
    raise SystemExit(f"{name} did not become reachable at {host}:{port}: {last_error}")


for target in TARGETS:
    wait(*target)
