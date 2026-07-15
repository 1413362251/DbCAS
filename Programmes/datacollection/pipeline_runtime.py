import csv
import json
import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


DATETIME_FORMAT = "%Y%m%d_%H%M%S"


@dataclass(frozen=True)
class RunPaths:
    run_id: str
    run_dir: Path
    checkpoints_dir: Path
    outputs_dir: Path
    logs_dir: Path
    state_path: Path
    config_path: Path
    log_path: Path


class RateLimiter:
    def __init__(self, calls_per_second: float):
        self.min_interval = 0.0 if calls_per_second <= 0 else 1.0 / calls_per_second
        self._last_call: Optional[float] = None
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self.min_interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            if self._last_call is not None:
                elapsed = now - self._last_call
                if elapsed < self.min_interval:
                    time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


class PipelineState:
    def __init__(self, path: Path, data: Dict[str, Any]):
        self.path = path
        self.data = data
        self._normalize()

    @classmethod
    def load_or_create(cls, path: Path, run_id: str) -> "PipelineState":
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            now = datetime.now().isoformat(timespec="seconds")
            data = {
                "run_id": run_id,
                "created_at": now,
                "updated_at": now,
                "current_stage": None,
                "completed_stages": [],
                "completed_keys": {},
                "counts": {},
                "failures": {},
                "output_paths": {},
            }
        state = cls(path, data)
        state.save()
        return state

    def _normalize(self) -> None:
        self.data.setdefault("completed_stages", [])
        self.data.setdefault("completed_keys", {})
        self.data.setdefault("counts", {})
        self.data.setdefault("failures", {})
        self.data.setdefault("output_paths", {})

    def save(self) -> None:
        self.data["updated_at"] = datetime.now().isoformat(timespec="seconds")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_text(
            json.dumps(self.data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def set_current_stage(self, stage: Optional[str]) -> None:
        self.data["current_stage"] = stage
        self.save()

    def mark_stage_complete(self, stage: str) -> None:
        completed = self.data.setdefault("completed_stages", [])
        if stage not in completed:
            completed.append(stage)
        if self.data.get("current_stage") == stage:
            self.data["current_stage"] = None
        self.save()

    def stage_completed(self, stage: str) -> bool:
        return stage in set(self.data.get("completed_stages", []))

    def completed_keys(self, key_group: str) -> Set[str]:
        return set(self.data.setdefault("completed_keys", {}).setdefault(key_group, []))

    def mark_key_complete(self, key_group: str, key: str) -> None:
        self.mark_keys_complete(key_group, [key])

    def mark_keys_complete(self, key_group: str, keys_to_mark: Iterable[str]) -> None:
        keys = self.data.setdefault("completed_keys", {}).setdefault(key_group, [])
        existing = set(keys)
        changed = False
        for key in keys_to_mark:
            if key and key not in existing:
                keys.append(key)
                existing.add(key)
                changed = True
        if not changed:
            return
        self.save()

    def record_failure(self, key_group: str, key: str, error: str) -> None:
        failures = self.data.setdefault("failures", {}).setdefault(key_group, [])
        failures.append(
            {
                "key": key,
                "error": error,
                "at": datetime.now().isoformat(timespec="seconds"),
            }
        )
        self.save()

    def set_count(self, name: str, value: int) -> None:
        self.data.setdefault("counts", {})[name] = int(value)
        self.save()

    def set_output_path(self, name: str, path: Path) -> None:
        self.data.setdefault("output_paths", {})[name] = str(path)
        self.save()


def make_run_id() -> str:
    return datetime.now().strftime(DATETIME_FORMAT)


def setup_run_paths(base_dir: Path, run_id: Optional[str], resume: bool) -> RunPaths:
    resolved_run_id = run_id.strip() if run_id else make_run_id()
    run_dir = base_dir / "runs" / resolved_run_id
    if run_dir.exists() and not resume:
        raise FileExistsError(
            f"Run directory already exists: {run_dir}. Use resume=True or choose a new RUN_ID."
        )

    checkpoints_dir = run_dir / "checkpoints"
    outputs_dir = run_dir / "outputs"
    logs_dir = run_dir / "logs"
    for directory in (run_dir, checkpoints_dir, outputs_dir, logs_dir):
        directory.mkdir(parents=True, exist_ok=True)

    return RunPaths(
        run_id=resolved_run_id,
        run_dir=run_dir,
        checkpoints_dir=checkpoints_dir,
        outputs_dir=outputs_dir,
        logs_dir=logs_dir,
        state_path=run_dir / "state.json",
        config_path=run_dir / "config.json",
        log_path=logs_dir / "pipeline.log",
    )


def configure_logging(log_path: Path) -> logging.Logger:
    logger = logging.getLogger("searchscreening_pipeline")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(stream_handler)
    return logger


def write_config(config_path: Path, config: Dict[str, Any]) -> None:
    config_path.write_text(
        json.dumps(config, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )


def read_csv_rows(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def append_csv_row(path: Path, row: Dict[str, Any], fieldnames: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exists = path.exists()
    filtered_row = {field: row.get(field, "") for field in fieldnames}
    with path.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(filtered_row)


def append_csv_rows(path: Path, rows: Iterable[Dict[str, Any]], fieldnames: Sequence[str]) -> None:
    for row in rows:
        append_csv_row(path, row, fieldnames)


def clean_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        if value != value:
            return ""
    except TypeError:
        pass
    return str(value).strip()


def to_int(value: Any, default: int = 0) -> int:
    try:
        if value == "":
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default
