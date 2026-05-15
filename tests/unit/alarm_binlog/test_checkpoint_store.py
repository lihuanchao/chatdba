import json
from pathlib import Path

from chatdba.alarm_binlog.checkpoint_store import AlarmCheckpointStore


def test_checkpoint_store_returns_zero_when_file_missing(tmp_path: Path):
    store = AlarmCheckpointStore(tmp_path / "missing.json")

    assert store.load() == 0


def test_checkpoint_store_saves_last_success_id_atomically(tmp_path: Path):
    path = tmp_path / "checkpoint.json"
    store = AlarmCheckpointStore(path)

    store.save(42)

    assert store.load() == 42
    assert json.loads(path.read_text(encoding="utf-8")) == {"last_success_id": 42}
