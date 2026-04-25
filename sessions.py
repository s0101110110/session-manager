"""Session Manager — управление сессиями Claude Code."""
import json
import os
import shutil
import subprocess
import sys
import argparse
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


class Logger:
    """Writes structured log entries to a file. Used by all classes."""

    def __init__(self, log_path: Path, debug: bool = False):
        self.log_path = Path(log_path)
        self.debug_enabled = debug
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

    def _write(self, level: str, message: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        line = f"{ts} {level} {message}\n"
        with open(self.log_path, "a") as f:
            f.write(line)

    def info(self, message: str) -> None:
        self._write("INFO", message)

    def error(self, message: str) -> None:
        self._write("ERROR", message)

    def debug(self, message: str) -> None:
        if self.debug_enabled:
            self._write("DEBUG", message)
