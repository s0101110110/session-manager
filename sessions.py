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


@dataclass
class Session:
    id: str
    project: str
    file_path: Optional[Path]
    size_bytes: int = 0
    message_count: int = 0
    first_message: str = ""
    last_modified: float = 0.0
    is_ghost: bool = False
    is_corrupted: bool = False


class SessionStore:
    """Reads and indexes Claude Code sessions across all projects."""

    def __init__(self, claude_home: Path):
        self.claude_home = Path(claude_home)
        self.projects_dir = self.claude_home / "projects"
        self.history_file = self.claude_home / "history.jsonl"

    def list_sessions(self) -> List[Session]:
        sessions = []
        seen_ids = set()

        if self.projects_dir.exists():
            for project_dir in self.projects_dir.iterdir():
                if not project_dir.is_dir():
                    continue
                for jsonl_file in project_dir.glob("*.jsonl"):
                    s = self._read_session(jsonl_file, project_dir.name)
                    sessions.append(s)
                    seen_ids.add(s.id)

        if self.history_file.exists():
            for ghost_id, ghost_project, ghost_msg in self._scan_history():
                if ghost_id not in seen_ids:
                    sessions.append(Session(
                        id=ghost_id,
                        project=ghost_project,
                        file_path=None,
                        first_message=ghost_msg,
                        is_ghost=True,
                    ))
                    seen_ids.add(ghost_id)

        return sessions

    def _read_session(self, path: Path, project: str) -> Session:
        session_id = path.stem
        size = path.stat().st_size
        mtime = path.stat().st_mtime
        try:
            count = 0
            first_msg = ""
            with open(path) as f:
                for line in f:
                    if not line.strip():
                        continue
                    d = json.loads(line)
                    count += 1
                    if not first_msg and d.get("type") == "user":
                        content = d.get("message", {}).get("content", "")
                        if isinstance(content, str):
                            first_msg = content[:200]
                        elif isinstance(content, list):
                            for m in content:
                                if isinstance(m, dict) and m.get("type") == "text":
                                    first_msg = m.get("text", "")[:200]
                                    break
            return Session(
                id=session_id,
                project=project,
                file_path=path,
                size_bytes=size,
                message_count=count,
                first_message=first_msg,
                last_modified=mtime,
            )
        except (json.JSONDecodeError, ValueError):
            return Session(
                id=session_id,
                project=project,
                file_path=path,
                size_bytes=size,
                last_modified=mtime,
                is_corrupted=True,
            )

    def _scan_history(self):
        seen = set()
        with open(self.history_file) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    sid = d.get("sessionId", "")
                    if not sid or sid in seen:
                        continue
                    seen.add(sid)
                    project_path = d.get("project", "")
                    project_dirname = "-" + project_path.replace("/", "-").lstrip("-")
                    yield sid, project_dirname, d.get("display", "")[:200]
                except json.JSONDecodeError:
                    continue
