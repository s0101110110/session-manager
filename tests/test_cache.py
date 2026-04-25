import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from sessions import NameCache


def test_set_and_get(tmp_path):
    cache = NameCache(tmp_path / "names.json")
    cache.set("abc123", name="Test Session", summary="A test", file_size=100)
    entry = cache.get("abc123")
    assert entry["name"] == "Test Session"
    assert entry["summary"] == "A test"
    assert entry["file_size"] == 100


def test_persists_to_disk(tmp_path):
    path = tmp_path / "names.json"
    cache1 = NameCache(path)
    cache1.set("xyz", name="X", summary="Y", file_size=50)

    cache2 = NameCache(path)
    assert cache2.get("xyz")["name"] == "X"


def test_invalidation_when_size_changed(tmp_path):
    cache = NameCache(tmp_path / "names.json")
    cache.set("abc", name="Old", summary="Old", file_size=100)
    assert cache.is_valid("abc", current_size=100) is True
    assert cache.is_valid("abc", current_size=200) is False


def test_get_missing(tmp_path):
    cache = NameCache(tmp_path / "names.json")
    assert cache.get("nope") is None


def test_delete_entry(tmp_path):
    cache = NameCache(tmp_path / "names.json")
    cache.set("abc", name="X", summary="Y", file_size=10)
    cache.delete("abc")
    assert cache.get("abc") is None
