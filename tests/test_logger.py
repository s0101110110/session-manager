import os
import tempfile
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import Logger


def test_logger_writes_to_file(tmp_path):
    log_file = tmp_path / "test.log"
    logger = Logger(log_file)
    logger.error("test error message")
    content = log_file.read_text()
    assert "test error message" in content
    assert "ERROR" in content


def test_logger_includes_timestamp(tmp_path):
    log_file = tmp_path / "test.log"
    logger = Logger(log_file)
    logger.info("hello")
    content = log_file.read_text()
    # Format: 2026-04-25 18:15:30 INFO hello
    assert "2026" in content


def test_logger_debug_only_when_enabled(tmp_path):
    log_file = tmp_path / "test.log"
    logger = Logger(log_file, debug=False)
    logger.debug("hidden")
    assert "hidden" not in log_file.read_text() if log_file.exists() else True

    logger2 = Logger(log_file, debug=True)
    logger2.debug("visible")
    assert "visible" in log_file.read_text()
