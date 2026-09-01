from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Event:
    role: str
    text: str
    ts: str
    message_id: str | None = None
    source: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


class TranscriptStore:
    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.scenarios_dir = run_dir / "scenarios"
        self.scenarios_dir.mkdir(parents=True, exist_ok=True)
        self.events_path = run_dir / "transcripts.jsonl"

    def write_run_meta(self, meta: dict[str, Any]) -> None:
        (self.run_dir / "run.json").write_text(json.dumps(meta, indent=2, default=str))

    def write_scenario(self, payload: dict[str, Any]) -> None:
        path = self.scenarios_dir / f"{payload['scenario_id']}.json"
        path.write_text(json.dumps(payload, indent=2, default=str))

    def append_events(self, scenario_id: str, events: list[Event]) -> None:
        with self.events_path.open("a") as fh:
            for event in events:
                record = {"scenario_id": scenario_id, **asdict(event)}
                fh.write(json.dumps(record) + "\n")
