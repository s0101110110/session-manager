import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import HealthCheck


def test_healthy_environment(fake_claude_home):
    hc = HealthCheck(fake_claude_home)
    with patch("shutil.which", return_value="/usr/local/bin/claude"):
        report = hc.run()
    assert report["projects_dir_ok"] is True
    assert report["claude_cli_ok"] is True


def test_missing_projects_dir(tmp_path):
    hc = HealthCheck(tmp_path)
    report = hc.run()
    assert report["projects_dir_ok"] is False
    assert "auto_fix" in report


def test_corrupted_session_reported(fake_claude_home):
    hc = HealthCheck(fake_claude_home)
    report = hc.run()
    assert report["corrupted_count"] == 1


def test_claude_cli_missing(fake_claude_home):
    hc = HealthCheck(fake_claude_home)
    with patch("shutil.which", return_value=None):
        report = hc.run()
    assert report["claude_cli_ok"] is False
