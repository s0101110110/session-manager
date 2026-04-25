import json
import pytest
from pathlib import Path


@pytest.fixture
def fake_claude_home(tmp_path):
    """Creates a fake ~/.claude directory structure."""
    claude_home = tmp_path / ".claude"
    projects = claude_home / "projects" / "-Users-test"
    projects.mkdir(parents=True)

    # Real session file
    session1 = projects / "abc12345-1111-2222-3333-444455556666.jsonl"
    with open(session1, "w") as f:
        for msg in [
            {"type": "user", "message": {"content": "первый вопрос"}},
            {"type": "assistant", "message": {"content": "ответ"}},
            {"type": "user", "message": {"content": "второй вопрос"}},
        ]:
            f.write(json.dumps(msg) + "\n")

    # Corrupted session
    bad = projects / "ddd99999-aaaa-bbbb-cccc-dddddddddddd.jsonl"
    bad.write_text("{not valid json\n{also bad\n")

    # history.jsonl with ghost session reference
    history = claude_home / "history.jsonl"
    with open(history, "w") as f:
        f.write(json.dumps({
            "sessionId": "abc12345-1111-2222-3333-444455556666",
            "display": "первый вопрос",
            "timestamp": 1777000000000,
            "project": "/Users/test"
        }) + "\n")
        # Ghost session — in history but no file
        f.write(json.dumps({
            "sessionId": "ghost1234-0000-0000-0000-000000000000",
            "display": "ghost message",
            "timestamp": 1777000010000,
            "project": "/Users/test"
        }) + "\n")

    return claude_home
