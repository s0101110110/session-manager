import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import SessionStore, NameCache, Operations


def test_delete_removes_file(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"
    ops.delete(sid)
    sessions = store.list_sessions()
    real_ids = [s.id for s in sessions if not s.is_ghost]
    assert sid not in real_ids


def test_delete_removes_cache_entry(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    cache.set("abc12345-1111-2222-3333-444455556666", name="X", summary="Y", file_size=100)
    ops = Operations(store, cache)
    ops.delete("abc12345-1111-2222-3333-444455556666")
    assert cache.get("abc12345-1111-2222-3333-444455556666") is None


def test_delete_ghost_removes_from_history(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    ops.delete("ghost1234-0000-0000-0000-000000000000")
    history_lines = (fake_claude_home / "history.jsonl").read_text().splitlines()
    for line in history_lines:
        if line.strip():
            d = json.loads(line)
            assert d["sessionId"] != "ghost1234-0000-0000-0000-000000000000"


def test_rename_updates_cache(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"
    ops.rename(sid, "Моё новое название")
    assert cache.get(sid)["name"] == "Моё новое название"


def test_rename_does_not_modify_jsonl_file(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"
    ops.rename(sid, "X")
    sessions_after = store.list_sessions()
    ids_after = [s.id for s in sessions_after if not s.is_ghost and not s.is_corrupted]
    assert sid in ids_after


def test_move_to_different_project(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"

    target_project = fake_claude_home / "projects" / "-Users-other"
    target_project.mkdir(parents=True)

    ops.move(sid, "-Users-other")

    assert (target_project / f"{sid}.jsonl").exists()
    assert not (fake_claude_home / "projects" / "-Users-test" / f"{sid}.jsonl").exists()


def test_move_rollback_on_target_missing(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"
    original = fake_claude_home / "projects" / "-Users-test" / f"{sid}.jsonl"
    assert original.exists()

    try:
        ops.move(sid, "-Users-nonexistent")
        assert False, "Should have raised"
    except (ValueError, FileNotFoundError):
        pass

    assert original.exists()


def test_move_ghost_session_raises(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)

    target = fake_claude_home / "projects" / "-Users-other"
    target.mkdir(parents=True)

    try:
        ops.move("ghost1234-0000-0000-0000-000000000000", "-Users-other")
        assert False, "Should have raised"
    except ValueError as e:
        assert "ghost" in str(e).lower()


def test_export_creates_markdown(fake_claude_home, tmp_path):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"

    out = ops.export(sid, output_dir=tmp_path)

    assert out.exists()
    assert out.suffix == ".md"
    content = out.read_text()
    assert "первый вопрос" in content
    assert "USER" in content or "user" in content.lower()


def test_export_uses_cached_name(fake_claude_home, tmp_path):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    sid = "abc12345-1111-2222-3333-444455556666"
    cache.set(sid, name="Моё-название", summary="x", file_size=100)
    ops = Operations(store, cache)

    out = ops.export(sid, output_dir=tmp_path)
    assert "Моё-название" in out.name


def test_continue_invokes_claude_resume(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)
    sid = "abc12345-1111-2222-3333-444455556666"

    with patch("os.execvp") as mock_exec:
        ops.continue_session(sid)

    args, _ = mock_exec.call_args
    assert args[0] == "claude"
    assert "--resume" in args[1]
    assert sid in args[1]


def test_continue_ghost_raises(fake_claude_home):
    store = SessionStore(fake_claude_home)
    cache = NameCache(fake_claude_home / "session-backups" / "names.json")
    ops = Operations(store, cache)

    try:
        ops.continue_session("ghost1234-0000-0000-0000-000000000000")
        assert False, "Should have raised"
    except ValueError:
        pass
