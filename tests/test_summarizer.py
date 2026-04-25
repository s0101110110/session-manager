import sys
from pathlib import Path
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import Summarizer


def test_summarize_calls_claude_cli():
    summarizer = Summarizer(model="claude-haiku-4-5")
    fake_response = '{"name": "Тест", "summary": "Краткое описание тестовой сессии"}'

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout=fake_response, returncode=0)
        result = summarizer.summarize(["первое сообщение", "второе"])

    assert result["name"] == "Тест"
    assert "тестовой" in result["summary"]
    args, kwargs = mock_run.call_args
    assert "claude" in args[0][0]
    assert "claude-haiku-4-5" in " ".join(args[0])


def test_summarize_handles_invalid_json():
    summarizer = Summarizer()
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout="not json at all", returncode=0)
        result = summarizer.summarize(["msg"])
    assert result["name"] == "(без названия)"
    assert result["summary"] == ""


def test_summarize_handles_cli_error():
    summarizer = Summarizer()
    with patch("subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("claude not found")
        result = summarizer.summarize(["msg"])
    assert result["name"] == "(без названия)"


def test_summarize_truncates_long_messages():
    summarizer = Summarizer()
    long_msg = "x" * 5000
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(stdout='{"name":"X","summary":"Y"}', returncode=0)
        summarizer.summarize([long_msg])
        prompt = mock_run.call_args[1].get("input", "")
        assert len(prompt) < 5000
