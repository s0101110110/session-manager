import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import SessionStore


def test_lists_real_sessions(fake_claude_home):
    store = SessionStore(fake_claude_home)
    sessions = store.list_sessions()
    ids = [s.id for s in sessions]
    assert "abc12345-1111-2222-3333-444455556666" in ids


def test_detects_ghost_sessions(fake_claude_home):
    store = SessionStore(fake_claude_home)
    sessions = store.list_sessions()
    ghosts = [s for s in sessions if s.is_ghost]
    assert len(ghosts) == 1
    assert ghosts[0].id == "ghost1234-0000-0000-0000-000000000000"


def test_detects_corrupted_sessions(fake_claude_home):
    store = SessionStore(fake_claude_home)
    sessions = store.list_sessions()
    corrupted = [s for s in sessions if s.is_corrupted]
    assert len(corrupted) == 1


def test_session_metadata(fake_claude_home):
    store = SessionStore(fake_claude_home)
    sessions = store.list_sessions()
    real = [s for s in sessions if not s.is_ghost and not s.is_corrupted][0]
    assert real.message_count == 3  # 2 user + 1 assistant
    assert real.size_bytes > 0
    assert real.project == "-Users-test"


def test_first_user_message(fake_claude_home):
    store = SessionStore(fake_claude_home)
    sessions = store.list_sessions()
    real = [s for s in sessions if not s.is_ghost and not s.is_corrupted][0]
    assert "первый вопрос" in real.first_message
