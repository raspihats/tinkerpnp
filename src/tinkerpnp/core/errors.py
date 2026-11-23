"""Errors raised by the machine core. `name` is an API error name (docs/api.md, section 4)."""
from __future__ import annotations

from typing import Any


class MachineError(Exception):
    def __init__(self, name: str, message: str, **data: Any):
        super().__init__(message)
        self.name = name
        self.message = message
        self.data = data


class Stopped(MachineError):
    def __init__(self, message: str = "stopped"):
        super().__init__("stopped", message)
