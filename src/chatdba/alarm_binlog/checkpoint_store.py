from __future__ import annotations

import json
from pathlib import Path


class AlarmCheckpointStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> int:
        if not self.path.exists():
            return 0
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return int(payload["last_success_id"])

    def save(self, last_success_id: int) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps({"last_success_id": int(last_success_id)}),
            encoding="utf-8",
        )
        temp_path.replace(self.path)
