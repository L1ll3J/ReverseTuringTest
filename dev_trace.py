"""Opt-in local development records, separate from character knowledge."""

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from hashlib import sha256
from importlib import metadata
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
from time import monotonic_ns
from uuid import uuid4
import warnings


def development_mode():
    return os.getenv("ALIBAI_DEV_MODE") == "1"


def _json_value(value):
    if is_dataclass(value) and not isinstance(value, type):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("Trace object keys must be strings")
        return {key: _json_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError("Unsupported development trace value")


def prompt_fingerprints(root):
    result = {}
    for name in ("System Prompt.txt", "prompts/speech.xml", "prompts/vote.xml", "prompts/director.xml", "prompts/muffled.xml",
                 "prompts/current_infatuation.xml", "prompts/current_bitterness.xml"):
        try:
            result[name] = sha256(Path(root, name).read_bytes()).hexdigest()
        except OSError:
            result[name] = None
    return result


def source_fingerprint(root):
    result = {"revision": None, "dirty": None, "frozen": bool(getattr(sys, "frozen", False))}
    revision = os.getenv("ALIBAI_BUILD_REVISION", "")
    if re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        result["revision"] = revision.lower()
        return result
    if result["frozen"]:
        return result
    command = ["git", "-c", "safe.directory=" + str(Path(root).resolve())]
    options = {"cwd": root, "capture_output": True, "timeout": 2,
               "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    try:
        head = subprocess.run(command + ["rev-parse", "HEAD"], **options)
        revision = head.stdout.decode("ascii").strip()
        if head.returncode == 0 and re.fullmatch(r"[0-9a-fA-F]{40}", revision):
            result["revision"] = revision.lower()
            dirty = subprocess.run(command + ["diff", "--quiet", "HEAD"], **options)
            result["dirty"] = dirty.returncode != 0 if dirty.returncode in (0, 1) else None
    except (OSError, UnicodeError, subprocess.TimeoutExpired):
        pass
    return result


@dataclass(frozen=True)
class TraceScope:
    run: "TraceRun"
    job_id: str
    attempt: int = 1
    parent_job_id: str | None = None

    def retry(self):
        return TraceScope(self.run, self.job_id, self.attempt + 1, self.parent_job_id)

    def record(self, kind, payload, *, request_id=None):
        self.run.record(kind, payload, scope=self, request_id=request_id)


def call_with_trace(operation, *args, trace_scope=None):
    if trace_scope is None:
        return operation(*args)
    return operation(*args, trace_scope=trace_scope)


class TraceRun:
    def __init__(self, enabled=None, root=None, on_error=None):
        self.enabled = development_mode() if enabled is None else bool(enabled)
        configured = os.getenv("ALIBAI_TRACE_DIR")
        default = Path(os.getenv("LOCALAPPDATA") or Path.home() / ".local" / "share") / "AlibAi" / "dev-runs"
        self.root = Path(root if root is not None else configured or default)
        self.on_error = on_error
        self.run_id = None
        self.run_dir = None
        self._sequence = 0
        self._lock = Lock()
        self._ended = False
        self._notified = False

    def start(self, manifest):
        if not self.enabled or self.run_id is not None:
            return
        notice = False
        with self._lock:
            if not self.enabled or self.run_id is not None:
                return
            try:
                bundle = getattr(sys, "_MEIPASS", None)
                if (getattr(sys, "frozen", False) and bundle is not None
                        and self.root.resolve().is_relative_to(Path(bundle).resolve())):
                    raise ValueError("Development runs cannot be stored inside the application bundle")
                self.run_id = uuid4().hex
                started = datetime.now(timezone.utc)
                self.run_dir = self.root / (started.strftime("%Y%m%dT%H%M%S") + "-" + self.run_id)
                self.run_dir.mkdir(parents=True, exist_ok=False)
                packages = {}
                for package in ("openai", "pydantic", "PyQt5"):
                    try:
                        packages[package] = metadata.version(package)
                    except metadata.PackageNotFoundError:
                        packages[package] = None
                data = {"schema_version": 1, "run_id": self.run_id,
                        "created_at": started.isoformat(), "python": sys.version.split()[0],
                        "packages": packages, "capture": "local_development",
                        "metadata": _json_value(manifest)}
                self.run_dir.joinpath("manifest.json").write_text(
                    json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8"
                )
                self.run_dir.joinpath("events.jsonl").touch(exist_ok=False)
            except (OSError, TypeError, ValueError, RecursionError):
                notice = self._disable_locked()
        if notice:
            self._notify()
        self.record("run_started", {})

    def new_job(self, task, *, parent_job_id=None, metadata=None):
        if not self.enabled or self.run_id is None:
            return None
        scope = TraceScope(self, uuid4().hex, 1, parent_job_id)
        scope.record("job_created", {"task": task, **(metadata or {})})
        return scope

    def record(self, kind, payload, *, scope=None, request_id=None):
        if not self.enabled or self.run_id is None:
            return
        notice = False
        with self._lock:
            if not self.enabled:
                return
            try:
                self._sequence += 1
                data = {"schema_version": 1, "run_id": self.run_id,
                        "sequence": self._sequence, "at": datetime.now(timezone.utc).isoformat(),
                        "monotonic_ns": monotonic_ns(), "kind": kind, "payload": _json_value(payload)}
                if scope is not None:
                    if scope.run is not self:
                        raise ValueError("A trace scope belongs to another run")
                    data.update(job_id=scope.job_id, attempt=scope.attempt,
                                parent_job_id=scope.parent_job_id)
                if request_id is not None:
                    data["request_id"] = request_id
                with self.run_dir.joinpath("events.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
                    stream.write(json.dumps(data, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
            except (OSError, TypeError, ValueError, RecursionError):
                notice = self._disable_locked()
        if notice:
            self._notify()

    def snapshot(self, state, *, reason):
        self.record("state_snapshot", {"reason": reason, "state": state})

    def end(self, reason):
        with self._lock:
            if self._ended:
                return
            self._ended = True
        self.record("run_ended", {"reason": reason})

    def _disable_locked(self):
        self.enabled = False
        if self._notified:
            return False
        self._notified = True
        return True

    def _notify(self):
        message = "Development trace recording stopped. Gameplay continues."
        try:
            if self.on_error is not None:
                self.on_error(message)
            else:
                warnings.warn(message, RuntimeWarning, stacklevel=2)
        except Exception:
            pass
