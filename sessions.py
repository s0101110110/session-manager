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


class NameCache:
    """Persistent cache for session names and summaries."""

    def __init__(self, cache_path: Path):
        self.cache_path = Path(cache_path)
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = self._load()

    def _load(self) -> dict:
        if not self.cache_path.exists():
            return {}
        try:
            with open(self.cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}

    def _save(self) -> None:
        tmp = self.cache_path.with_suffix(".json.tmp")
        with open(tmp, "w") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        tmp.replace(self.cache_path)

    def set(self, session_id: str, name: str, summary: str, file_size: int) -> None:
        self._data[session_id] = {
            "name": name,
            "summary": summary,
            "file_size": file_size,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        self._save()

    def get(self, session_id: str) -> Optional[dict]:
        return self._data.get(session_id)

    def is_valid(self, session_id: str, current_size: int) -> bool:
        entry = self._data.get(session_id)
        if not entry:
            return False
        return entry.get("file_size") == current_size

    def delete(self, session_id: str) -> None:
        if session_id in self._data:
            del self._data[session_id]
            self._save()


class Summarizer:
    """Generates session names and summaries via claude CLI."""

    DEFAULT_MODEL = "claude-haiku-4-5"
    PROMPT_TEMPLATE = (
        "Прочитай первые сообщения из сессии Claude Code и верни JSON:\n"
        '{{"name": "название из 3-5 слов на русском", '
        '"summary": "2-3 предложения о теме на русском"}}\n\n'
        "Сообщения:\n{messages}\n\n"
        "Верни ТОЛЬКО JSON без пояснений."
    )
    FALLBACK = {"name": "(без названия)", "summary": ""}

    def __init__(self, model: str = None, timeout: int = 30):
        self.model = model or self.DEFAULT_MODEL
        self.timeout = timeout

    def summarize(self, messages: list) -> dict:
        truncated = [m[:300] for m in messages[:10]]
        joined = "\n---\n".join(truncated)
        prompt = self.PROMPT_TEMPLATE.format(messages=joined[:2000])

        try:
            proc = subprocess.run(
                ["claude", "-p", "--model", self.model],
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            if proc.returncode != 0:
                return dict(self.FALLBACK)
            return self._parse(proc.stdout)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            return dict(self.FALLBACK)

    def _parse(self, output: str) -> dict:
        try:
            start = output.find("{")
            end = output.rfind("}")
            if start == -1 or end == -1:
                return dict(self.FALLBACK)
            data = json.loads(output[start:end + 1])
            return {
                "name": str(data.get("name", "(без названия)"))[:80],
                "summary": str(data.get("summary", ""))[:500],
            }
        except (json.JSONDecodeError, ValueError):
            return dict(self.FALLBACK)


class Operations:
    """Atomic operations on sessions: delete, rename, move, export, continue."""

    def __init__(self, store: "SessionStore", cache: "NameCache"):
        self.store = store
        self.cache = cache

    def delete(self, session_id: str) -> None:
        sessions = self.store.list_sessions()
        target = next((s for s in sessions if s.id == session_id), None)
        if target is None:
            raise ValueError(f"Session not found: {session_id}")

        if target.is_ghost:
            self._remove_from_history(session_id)
        else:
            if target.file_path and target.file_path.exists():
                target.file_path.unlink()

        self.cache.delete(session_id)

    def rename(self, session_id: str, new_name: str) -> None:
        existing = self.cache.get(session_id) or {}
        self.cache.set(
            session_id,
            name=new_name,
            summary=existing.get("summary", ""),
            file_size=existing.get("file_size", 0),
        )

    def move(self, session_id: str, target_project: str) -> None:
        sessions = self.store.list_sessions()
        target = next((s for s in sessions if s.id == session_id), None)
        if target is None:
            raise ValueError(f"Session not found: {session_id}")
        if target.is_ghost:
            raise ValueError(f"Cannot move ghost session: {session_id}")

        target_dir = self.store.projects_dir / target_project
        if not target_dir.exists() or not target_dir.is_dir():
            raise FileNotFoundError(f"Target project does not exist: {target_project}")

        src = target.file_path
        dst = target_dir / src.name

        if dst.exists():
            raise FileExistsError(f"Target already has a session with this id: {dst}")

        src.replace(dst)

    def export(self, session_id: str, output_dir: Path) -> Path:
        sessions = self.store.list_sessions()
        target = next((s for s in sessions if s.id == session_id), None)
        if target is None or target.is_ghost or target.file_path is None:
            raise ValueError(f"Cannot export: {session_id}")

        cached = self.cache.get(session_id)
        name = (cached.get("name") if cached else None) or session_id[:8]
        safe_name = "".join(c if c.isalnum() or c in "-_ " else "_" for c in name)[:60]

        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        out = output_dir / f"{safe_name}.md"

        with open(out, "w") as f_out:
            f_out.write(f"# {name}\n\n")
            f_out.write(f"**Session ID:** `{session_id}`\n\n---\n\n")
            with open(target.file_path) as f_in:
                for line in f_in:
                    if not line.strip():
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    role = d.get("type", "?").upper()
                    content = d.get("message", {}).get("content", "")
                    text = self._extract_text(content)
                    if text:
                        f_out.write(f"### {role}\n\n{text}\n\n---\n\n")
        return out

    def continue_session(self, session_id: str) -> None:
        sessions = self.store.list_sessions()
        target = next((s for s in sessions if s.id == session_id), None)
        if target is None:
            raise ValueError(f"Session not found: {session_id}")
        if target.is_ghost:
            raise ValueError(f"Cannot resume ghost session: {session_id}")
        os.execvp("claude", ["claude", "--resume", session_id])

    def _remove_from_history(self, session_id: str) -> None:
        history = self.store.history_file
        if not history.exists():
            return
        kept = []
        with open(history) as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    if d.get("sessionId") != session_id:
                        kept.append(line.rstrip("\n"))
                except json.JSONDecodeError:
                    kept.append(line.rstrip("\n"))
        tmp = history.with_suffix(".jsonl.tmp")
        with open(tmp, "w") as f:
            for line in kept:
                f.write(line + "\n")
        tmp.replace(history)

    @staticmethod
    def _extract_text(content) -> str:
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for m in content:
                if isinstance(m, dict) and m.get("type") == "text":
                    parts.append(m.get("text", ""))
            return "\n".join(parts)
        return ""


class HealthCheck:
    """Validates environment at startup. Returns a report with auto-fix suggestions."""

    def __init__(self, claude_home: Path):
        self.claude_home = Path(claude_home)

    def run(self) -> dict:
        report = {
            "projects_dir_ok": False,
            "claude_cli_ok": False,
            "corrupted_count": 0,
            "ghost_count": 0,
            "issues": [],
            "auto_fix": [],
        }

        projects = self.claude_home / "projects"
        if projects.exists() and projects.is_dir():
            report["projects_dir_ok"] = True
        else:
            report["issues"].append(f"Projects directory missing: {projects}")
            report["auto_fix"].append(f"mkdir -p {projects}")

        if shutil.which("claude") is not None:
            report["claude_cli_ok"] = True
        else:
            report["issues"].append("claude CLI not found in PATH")
            report["auto_fix"].append("Install Claude Code: https://docs.claude.com/claude-code")

        store = SessionStore(self.claude_home)
        sessions = store.list_sessions()
        report["corrupted_count"] = sum(1 for s in sessions if s.is_corrupted)
        report["ghost_count"] = sum(1 for s in sessions if s.is_ghost)

        return report
