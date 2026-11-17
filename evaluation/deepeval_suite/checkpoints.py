"""Atomic metric checkpoints, configuration validation and single-judge locking."""

import fcntl
import hashlib
import json
import os
from pathlib import Path


def atomic_json(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w") as file:
        json.dump(value, file, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)


class Checkpoints:
    def __init__(self, output, fingerprint, endpoint):
        self.output = Path(output)
        self.folder = self.output / "checkpoints"
        self.folder.mkdir(exist_ok=True)
        path = self.output / "fingerprint.json"
        if path.exists() and json.loads(path.read_text()) != fingerprint:
            raise ValueError("Resume configuration/input changed; use a new output directory")
        atomic_json(path, fingerprint)
        locks = Path(__file__).resolve().parents[2] / ".deepeval" / "locks"
        locks.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(endpoint.encode()).hexdigest()
        self.lock = (locks / (key + ".lock")).open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise RuntimeError("Another evaluation is already using this judge endpoint") from None

    def path(self, case, metric):
        key = hashlib.sha256((case + "|" + metric).encode()).hexdigest()
        return self.folder / (key + ".json")

    def get(self, case, metric):
        path = self.path(case, metric)
        return json.loads(path.read_text()) if path.exists() else None

    def put(self, case, metric, record, attempts):
        previous = self.get(case, metric)
        history = previous.get("history", []) + [previous["record"]] if previous else []
        atomic_json(
            self.path(case, metric),
            {
                "case_id": case,
                "metric": metric,
                "record": record,
                "attempts": attempts,
                "history": history,
            },
        )

    def close(self):
        self.lock.close()
