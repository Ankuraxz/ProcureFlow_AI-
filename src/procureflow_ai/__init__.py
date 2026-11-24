"""ProcureFlow AI — Autonomous Enterprise Procurement & Spend Desk."""

from __future__ import annotations

from .cli import main
from .config import APP_NAME, Settings, get_settings

__version__ = "0.1.0"

__all__ = [
    "APP_NAME",
    "Settings",
    "__version__",
    "get_settings",
    "main",
]
