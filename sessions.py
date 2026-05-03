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
                    if s.first_message.startswith("Прочитай первые сообщения из сессии Claude Code"):
                        continue
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
                ["claude", "-p", "--no-session-persistence", "--model", self.model],
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


# ── TUI (Textual) ────────────────────────────────────────────────────────────

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import DataTable, Static, Footer, Header, Input, Label, Button
    from textual.binding import Binding
    from textual.screen import ModalScreen
    TEXTUAL_AVAILABLE = True
except ImportError:
    TEXTUAL_AVAILABLE = False

if TEXTUAL_AVAILABLE:

    class ConfirmDeleteScreen(ModalScreen):
        CSS = "ConfirmDeleteScreen { align: center middle; } Vertical { background: $surface; border: solid $primary; padding: 1 2; width: 50; height: auto; } Horizontal { height: 3; align: center middle; } Button { margin: 0 1; }"

        def __init__(self, session_id: str, name: str):
            super().__init__()
            self.session_id = session_id
            self.session_name = name

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label(f"Удалить сессию:\n{self.session_name}?")
                with Horizontal():
                    yield Button("Удалить", variant="error", id="confirm")
                    yield Button("Отмена", id="cancel")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            self.dismiss(event.button.id == "confirm")

    class RenameScreen(ModalScreen):
        CSS = "RenameScreen { align: center middle; } Vertical { background: $surface; border: solid $primary; padding: 1 2; width: 60; height: auto; } Horizontal { height: 3; align: center middle; } Button { margin: 0 1; }"

        def __init__(self, session_id: str, current_name: str):
            super().__init__()
            self.session_id = session_id
            self.current_name = current_name

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label("Новое название:")
                yield Input(value=self.current_name, id="new-name")
                with Horizontal():
                    yield Button("Сохранить", variant="primary", id="save")
                    yield Button("Отмена", id="cancel")

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "save":
                self.dismiss(self.query_one("#new-name", Input).value)
            else:
                self.dismiss(None)

    class MoveScreen(ModalScreen):
        CSS = "MoveScreen { align: center middle; } Vertical { background: $surface; border: solid $primary; padding: 1 2; width: 50; height: auto; } Horizontal { height: 3; align: center middle; } Button { margin: 0 1; }"

        def __init__(self, session_id: str, projects: list, current_project: str):
            super().__init__()
            self.session_id = session_id
            self.projects = projects
            self.current_project = current_project

        def compose(self) -> ComposeResult:
            with Vertical():
                yield Label("Переместить в проект:")
                yield DataTable(id="projects-table")
                with Horizontal():
                    yield Button("Переместить", variant="primary", id="move")
                    yield Button("Отмена", id="cancel")

        def on_mount(self) -> None:
            table = self.query_one("#projects-table", DataTable)
            table.add_columns("Проект")
            table.cursor_type = "row"
            for p in self.projects:
                marker = " (текущий)" if p == self.current_project else ""
                table.add_row(p + marker, key=p)

        def on_button_pressed(self, event: Button.Pressed) -> None:
            if event.button.id == "move":
                table = self.query_one("#projects-table", DataTable)
                if table.cursor_coordinate:
                    cell_key = table.coordinate_to_cell_key(table.cursor_coordinate)
                    self.dismiss(cell_key.row_key.value)
                else:
                    self.dismiss(None)
            else:
                self.dismiss(None)

    class TUIApp(App):
        """Session Manager TUI."""

        CSS = """
        #list-panel { width: 50%; border: solid $primary; }
        #preview-panel { width: 50%; border: solid $secondary; padding: 1; }
        #sessions-table { height: 1fr; }
        #preview-content { height: 1fr; }
        #search { dock: top; }
        """

        BINDINGS = [
            Binding("q", "quit", "Quit"),
            Binding("d", "delete_session", "Delete"),
            Binding("r", "rename_session", "Rename"),
            Binding("m", "move_session", "Move"),
            Binding("e", "export_session", "Export"),
            Binding("c", "continue_session_action", "Continue"),
            Binding("u", "refresh_summary", "Refresh summary"),
            Binding("slash", "focus_search", "Search"),
        ]

        def __init__(self, store, cache, summarizer, ops, logger):
            super().__init__()
            self.store = store
            self.cache = cache
            self.summarizer = summarizer
            self.ops = ops
            self.logger = logger
            self.sessions = []
            self._search_query = ""

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(id="list-panel"):
                    yield Input(placeholder="/ поиск...", id="search")
                    yield DataTable(id="sessions-table")
                with Vertical(id="preview-panel"):
                    yield Static("Выберите сессию для просмотра", id="preview-content")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#sessions-table", DataTable)
            table.add_columns("Название", "Дата", "Сбщ", "Размер")
            table.cursor_type = "row"
            self.refresh_sessions()
            self.run_worker(self._generate_missing_summaries, thread=True)

        def _generate_missing_summaries(self) -> None:
            for session in list(self.sessions):
                if session.is_ghost or session.is_corrupted:
                    continue
                if self.cache.is_valid(session.id, session.size_bytes):
                    continue
                try:
                    messages = self._read_first_messages(session.file_path, limit=10)
                    if not messages:
                        continue
                    result = self.summarizer.summarize(messages)
                    self.cache.set(
                        session.id,
                        name=result["name"],
                        summary=result["summary"],
                        file_size=session.size_bytes,
                    )
                    self.call_from_thread(self.refresh_sessions)
                except Exception as e:
                    self.logger.error(f"summary failed for {session.id}: {e}")

        def _read_first_messages(self, path: Path, limit: int = 10) -> list:
            messages = []
            try:
                with open(path) as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            d = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if d.get("type") != "user":
                            continue
                        content = d.get("message", {}).get("content", "")
                        text = Operations._extract_text(content)
                        if text:
                            messages.append(text[:300])
                        if len(messages) >= limit:
                            break
            except OSError:
                pass
            return messages

        def refresh_sessions(self, search_query: str = None) -> None:
            if search_query is not None:
                self._search_query = search_query.lower()

            all_sessions = self.store.list_sessions()
            all_sessions.sort(key=lambda s: s.last_modified, reverse=True)

            if self._search_query:
                filtered = []
                for s in all_sessions:
                    cached = self.cache.get(s.id) or {}
                    haystack = " ".join([
                        s.first_message,
                        s.id,
                        cached.get("name", ""),
                        cached.get("summary", ""),
                    ]).lower()
                    if self._search_query in haystack:
                        filtered.append(s)
                    elif s.file_path and s.file_path.exists():
                        try:
                            if self._search_query in s.file_path.read_text(errors="ignore").lower():
                                filtered.append(s)
                        except OSError:
                            pass
                self.sessions = filtered
            else:
                self.sessions = all_sessions

            table = self.query_one("#sessions-table", DataTable)
            table.clear()
            for s in self.sessions:
                cached = self.cache.get(s.id)
                name = (cached or {}).get("name") or s.first_message[:40] or s.id[:8]
                if s.is_ghost:
                    name = f"👻 {name}"
                elif s.is_corrupted:
                    name = f"⚠ {name}"
                date_str = (
                    datetime.fromtimestamp(s.last_modified).strftime("%Y-%m-%d %H:%M")
                    if s.last_modified else "—"
                )
                size_str = f"{s.size_bytes // 1024}K" if s.size_bytes else "—"
                table.add_row(name, date_str, str(s.message_count), size_str, key=s.id)

        def on_input_changed(self, event: "Input.Changed") -> None:
            if event.input.id == "search":
                self.refresh_sessions(search_query=event.value)

        def on_data_table_row_highlighted(self, event: "DataTable.RowHighlighted") -> None:
            if event.row_key is None:
                return
            sid = event.row_key.value
            session = next((s for s in self.sessions if s.id == sid), None)
            if not session:
                return
            cached = self.cache.get(sid) or {}
            preview = self.query_one("#preview-content", Static)
            date_str = (
                datetime.fromtimestamp(session.last_modified).strftime("%Y-%m-%d %H:%M")
                if session.last_modified else "—"
            )
            text = (
                f"📅 {date_str}\n"
                f"💬 {session.message_count} сообщений   "
                f"📦 {session.size_bytes // 1024}K\n"
                f"📁 {session.project}\n\n"
                f"{cached.get('summary') or session.first_message or '(нет данных)'}"
            )
            preview.update(text)

        def _selected_session(self):
            table = self.query_one("#sessions-table", DataTable)
            if table.cursor_coordinate is None:
                return None
            try:
                row_key = table.coordinate_to_cell_key(table.cursor_coordinate).row_key
                return next((s for s in self.sessions if s.id == row_key.value), None)
            except Exception:
                return None

        def action_delete_session(self) -> None:
            session = self._selected_session()
            if not session:
                return
            cached = self.cache.get(session.id) or {}
            name = cached.get("name") or session.first_message[:40] or session.id[:8]

            def after_confirm(confirmed: bool) -> None:
                if confirmed:
                    try:
                        self.ops.delete(session.id)
                        self.refresh_sessions()
                    except Exception as e:
                        self.logger.error(f"delete failed: {e}")
                        self.notify(f"Ошибка: {e}", severity="error")

            self.push_screen(ConfirmDeleteScreen(session.id, name), after_confirm)

        def action_rename_session(self) -> None:
            session = self._selected_session()
            if not session:
                return
            cached = self.cache.get(session.id) or {}
            current = cached.get("name") or ""

            def after_rename(new_name) -> None:
                if new_name and new_name.strip():
                    self.ops.rename(session.id, new_name.strip())
                    self.refresh_sessions()

            self.push_screen(RenameScreen(session.id, current), after_rename)

        def action_move_session(self) -> None:
            session = self._selected_session()
            if not session or session.is_ghost:
                self.notify("Невозможно переместить ghost-сессию", severity="warning")
                return

            projects = [p.name for p in self.store.projects_dir.iterdir() if p.is_dir()]

            def after_move(target) -> None:
                if target and target != session.project:
                    try:
                        self.ops.move(session.id, target)
                        self.refresh_sessions()
                    except Exception as e:
                        self.logger.error(f"move failed: {e}")
                        self.notify(f"Ошибка: {e}", severity="error")

            self.push_screen(MoveScreen(session.id, projects, session.project), after_move)

        def action_export_session(self) -> None:
            session = self._selected_session()
            if not session or session.is_ghost or session.is_corrupted:
                self.notify("Невозможно экспортировать", severity="warning")
                return
            try:
                out = self.ops.export(session.id, Path.home() / "Downloads")
                self.notify(f"Экспортировано: {out.name}")
            except Exception as e:
                self.logger.error(f"export failed: {e}")
                self.notify(f"Ошибка: {e}", severity="error")

        def action_continue_session_action(self) -> None:
            session = self._selected_session()
            if not session or session.is_ghost:
                self.notify("Невозможно продолжить", severity="warning")
                return
            self.exit()
            self.ops.continue_session(session.id)

        def action_focus_search(self) -> None:
            self.query_one("#search", Input).focus()

        def action_refresh_summary(self) -> None:
            session = self._selected_session()
            if not session or session.is_ghost or session.is_corrupted:
                return
            self.cache.delete(session.id)
            self.notify("Кэш очищен — резюме перегенерируется в фоне")
            self.run_worker(self._generate_missing_summaries, thread=True)


# ── CLI helpers ───────────────────────────────────────────────────────────────

def _resolve_paths():
    home = Path.home() / ".claude"
    cache_path = home / "session-backups" / "names.json"
    log_path = home / "session-backups" / "sessions.log"
    return home, cache_path, log_path


def cmd_list(store, cache):
    sessions = store.list_sessions()
    sessions.sort(key=lambda s: s.last_modified, reverse=True)
    print(f"{'ID':<10} {'Тип':<6} {'Проект':<35} Название")
    print("-" * 85)
    for s in sessions:
        cached = cache.get(s.id) or {}
        name = cached.get("name") or s.first_message[:40] or "(пусто)"
        kind = "GHOST" if s.is_ghost else "BAD" if s.is_corrupted else "OK"
        print(f"{s.id[:8]:<10} {kind:<6} {s.project[:35]:<35} {name[:40]}")


def cmd_delete(store, cache, session_id):
    ops = Operations(store, cache)
    matches = [s for s in store.list_sessions() if s.id.startswith(session_id)]
    if not matches:
        print(f"Сессия не найдена: {session_id}")
        return 1
    if len(matches) > 1:
        print(f"Несколько совпадений ({len(matches)}), уточните ID")
        return 1
    ops.delete(matches[0].id)
    print(f"Удалено: {matches[0].id}")
    return 0


def cmd_rename(store, cache, session_id, name):
    ops = Operations(store, cache)
    matches = [s for s in store.list_sessions() if s.id.startswith(session_id)]
    if not matches:
        print(f"Сессия не найдена: {session_id}")
        return 1
    ops.rename(matches[0].id, name)
    print(f"Переименовано: {matches[0].id} → {name}")
    return 0


def cmd_test():
    """Self-test mode: verify SessionStore and Operations on temp data."""
    import tempfile
    print("Запуск самопроверки...")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / ".claude"
        projects = home / "projects" / "-test-project"
        projects.mkdir(parents=True)
        sample = projects / "test1234-0000-0000-0000-000000000000.jsonl"
        sample.write_text(json.dumps({"type": "user", "message": {"content": "test"}}) + "\n")
        (home / "history.jsonl").touch()

        store = SessionStore(home)
        cache = NameCache(home / "session-backups" / "names.json")
        ops = Operations(store, cache)

        sessions = store.list_sessions()
        assert len(sessions) >= 1, "list_sessions failed"

        cache.set("test1234-0000-0000-0000-000000000000", name="X", summary="Y", file_size=10)
        assert cache.get("test1234-0000-0000-0000-000000000000") is not None

        ops.rename("test1234-0000-0000-0000-000000000000", "renamed")
        assert cache.get("test1234-0000-0000-0000-000000000000")["name"] == "renamed"

        ops.delete("test1234-0000-0000-0000-000000000000")
        assert not sample.exists(), "delete failed"

    print("✓ все тесты прошли")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Claude Code session manager")
    parser.add_argument("command", nargs="?", default="tui",
                        choices=["tui", "list", "delete", "rename", "health", "test"])
    parser.add_argument("session_id", nargs="?")
    parser.add_argument("name", nargs="?")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.command == "test":
        return cmd_test()

    home, cache_path, log_path = _resolve_paths()
    logger = Logger(log_path, debug=args.debug)
    store = SessionStore(home)
    cache = NameCache(cache_path)

    if args.command == "list":
        cmd_list(store, cache)
        return 0
    if args.command == "delete":
        if not args.session_id:
            print("Укажите ID сессии")
            return 1
        return cmd_delete(store, cache, args.session_id)
    if args.command == "rename":
        if not args.session_id or not args.name:
            print("Укажите ID и новое название")
            return 1
        return cmd_rename(store, cache, args.session_id, args.name)
    if args.command == "health":
        report = HealthCheck(home).run()
        for k, v in report.items():
            print(f"  {k}: {v}")
        return 0

    if not TEXTUAL_AVAILABLE:
        print("Textual не установлен. Запустите: pip3 install textual")
        return 1

    summarizer = Summarizer()
    ops = Operations(store, cache)
    app = TUIApp(store, cache, summarizer, ops, logger)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
