"""Local, ordered progression events and persistent content availability."""

from contextlib import contextmanager
from dataclasses import dataclass, field
import errno
import json
import math
import os
from pathlib import Path
import tempfile
import time
from types import MappingProxyType
from typing import Literal, Mapping

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ProfileError(Exception):
    """A local profile operation failed without discarding existing progress."""


def _require_text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Identifiers must be nonempty strings")


def _require_positive_integer(value):
    if type(value) is not int or value < 1:
        raise ValueError("Counts and event sequences must be positive integers")


def _count_mapping(values):
    if not isinstance(values, Mapping):
        raise ValueError("Progress counts must be a mapping")
    result = dict(values)
    for key, count in result.items():
        _require_text(key)
        if type(count) is not int or count < 0:
            raise ValueError("Progress counts must be nonnegative integers")
    return MappingProxyType(result)


def _content_ids(values):
    if not isinstance(values, (list, tuple, set, frozenset)):
        raise ValueError("Content identifiers must be a collection")
    for value in values:
        _require_text(value)
    return frozenset(values)


@dataclass(frozen=True)
class UnlockRule:
    event_type: str
    required_count: int

    def __post_init__(self):
        _require_text(self.event_type)
        _require_positive_integer(self.required_count)


@dataclass(frozen=True)
class ContentDefinition:
    content_id: str
    category: Literal["weapon", "item"]
    default: bool = False
    unlock_rule: UnlockRule | None = None

    def __post_init__(self):
        _require_text(self.content_id)
        if self.category not in ("weapon", "item") or type(self.default) is not bool:
            raise ValueError("Content requires a weapon or item category and a default flag")
        if self.unlock_rule is not None and not isinstance(self.unlock_rule, UnlockRule):
            raise ValueError("Content unlock rules must be UnlockRule values")


@dataclass(frozen=True)
class ProgressEvent:
    run_id: str
    sequence: int
    event_type: str
    amount: int = 1

    def __post_init__(self):
        _require_text(self.run_id)
        _require_text(self.event_type)
        _require_positive_integer(self.sequence)
        _require_positive_integer(self.amount)


@dataclass(frozen=True)
class ProfileSnapshot:
    counters: Mapping[str, int] = field(default_factory=dict)
    unlocked: frozenset[str] = frozenset()
    disabled_items: frozenset[str] = frozenset()
    last_sequences: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self):
        object.__setattr__(self, "counters", _count_mapping(self.counters))
        object.__setattr__(self, "last_sequences", _count_mapping(self.last_sequences))
        object.__setattr__(self, "unlocked", _content_ids(self.unlocked))
        object.__setattr__(self, "disabled_items", _content_ids(self.disabled_items))


@dataclass(frozen=True)
class ProgressUpdate:
    profile: ProfileSnapshot
    newly_unlocked: tuple[str, ...]
    applied: bool


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate profile field")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("Invalid JSON number")


class ProfileStore:
    """Catalogue order determines the order of returned content identifiers.

    read/load never changes profile data. record applies a contiguous event once
    and returns its resulting snapshot, newly unlocked IDs, and applied flag.
    Earlier/repeated sequences return applied=False without rewriting the file.
    Unlock rules are evaluated only when a new event is committed.
    """

    VERSION = 1

    def __init__(self, path=None, catalogue=(), lock_timeout=2.0):
        if path is None:
            local = os.getenv("LOCALAPPDATA")
            fallback = Path.home() / ("AppData/Local" if os.name == "nt" else ".local/share")
            root = Path(local) if local else fallback
            path = root / "AlibAi" / "profile.json"
        self.path = Path(path).expanduser().resolve()
        self.catalogue = tuple(catalogue)
        if any(not isinstance(item, ContentDefinition) for item in self.catalogue):
            raise ValueError("Catalogue entries must be ContentDefinition values")
        self._by_id = {item.content_id: item for item in self.catalogue}
        if len(self._by_id) != len(self.catalogue):
            raise ValueError("Content identifiers must be unique")
        if (type(lock_timeout) not in (int, float) or not math.isfinite(lock_timeout)
                or lock_timeout < 0):
            raise ValueError("Lock timeout must be finite and nonnegative")
        self.lock_timeout = lock_timeout

    def read(self):
        try:
            self.path.stat()
        except FileNotFoundError:
            return ProfileSnapshot()
        except OSError:
            raise ProfileError("Could not read the progression profile. It was left unchanged.") from None
        with self._locked():
            return self._read_document()[0]

    def load(self):
        return self.read()

    def record(self, event: ProgressEvent) -> ProgressUpdate:
        if not isinstance(event, ProgressEvent):
            raise ValueError("Progress requires a ProgressEvent")
        with self._locked():
            profile, document = self._read_document()
            previous = profile.last_sequences.get(event.run_id, 0)
            if event.sequence <= previous:
                return ProgressUpdate(profile, (), False)
            if event.sequence != previous + 1:
                raise ProfileError("Progress events must be recorded in order. Retry the missing event first.")
            counters = dict(profile.counters)
            counters[event.event_type] = counters.get(event.event_type, 0) + event.amount
            unlocked = set(profile.unlocked)
            newly_unlocked = []
            for content in self.catalogue:
                rule = content.unlock_rule
                if (not content.default and content.content_id not in unlocked and rule is not None
                        and counters.get(rule.event_type, 0) >= rule.required_count):
                    unlocked.add(content.content_id)
                    newly_unlocked.append(content.content_id)
            sequences = dict(profile.last_sequences)
            sequences[event.run_id] = event.sequence
            result = ProfileSnapshot(counters, unlocked, profile.disabled_items, sequences)
            self._write_document(result, document)
            return ProgressUpdate(result, tuple(newly_unlocked), True)

    def set_item_enabled(self, content_id, enabled):
        _require_text(content_id)
        if type(enabled) is not bool:
            raise ValueError("Item enabled state must be a boolean")
        content = self._by_id.get(content_id)
        if content is None or content.category != "item" or content.default:
            raise ProfileError("Only unlocked optional items can change their pool setting.")
        with self._locked():
            profile, document = self._read_document()
            if content_id not in profile.unlocked:
                raise ProfileError("Unlock this item before changing its pool setting.")
            disabled = set(profile.disabled_items)
            if enabled:
                disabled.discard(content_id)
            else:
                disabled.add(content_id)
            if disabled == profile.disabled_items:
                return profile
            result = ProfileSnapshot(profile.counters, profile.unlocked, disabled, profile.last_sequences)
            self._write_document(result, document)
            return result

    def available_weapons(self):
        profile = self.read()
        return tuple(item.content_id for item in self.catalogue
                     if item.category == "weapon" and (item.default or item.content_id in profile.unlocked))

    def item_pool(self):
        profile = self.read()
        return tuple(item.content_id for item in self.catalogue if item.category == "item"
                     and (item.default or (item.content_id in profile.unlocked
                                         and item.content_id not in profile.disabled_items)))

    def _read_document(self):
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return ProfileSnapshot(), {}
        except (OSError, UnicodeError):
            raise ProfileError("Could not read the progression profile. It was left unchanged.") from None
        try:
            document = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
            if not isinstance(document, dict):
                raise ValueError("Invalid profile root")
            if type(document.get("version")) is not int or document["version"] != self.VERSION:
                raise ProfileError("The progression profile uses an unsupported version. It was left unchanged.")
            profile = ProfileSnapshot(document["counters"], document["unlocked"],
                                      document["disabled_items"], document["last_sequences"])
            return profile, document
        except (ValueError, TypeError, KeyError):
            raise ProfileError("The progression profile is invalid. It was left unchanged.") from None

    def _write_document(self, profile, original):
        document = dict(original)
        document.update(version=self.VERSION, counters=dict(profile.counters),
                        unlocked=sorted(profile.unlocked), disabled_items=sorted(profile.disabled_items),
                        last_sequences=dict(profile.last_sequences))
        temporary = None
        try:
            descriptor, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(document, stream, ensure_ascii=False, allow_nan=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except (OSError, TypeError, ValueError):
            raise ProfileError("Could not save progression. Retry the same event safely.") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
                except OSError:
                    pass

    @contextmanager
    def _locked(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = open(self.path.with_name(self.path.name + ".lock"), "a+b")
        except OSError:
            raise ProfileError("Could not access the local progression profile.") from None
        acquired = False
        with stream:
            try:
                try:
                    stream.seek(0, os.SEEK_END)
                    if stream.tell() == 0:
                        stream.write(b"\0")
                        stream.flush()
                except OSError:
                    raise ProfileError("Could not access the local progression profile.") from None
                deadline = time.monotonic() + self.lock_timeout
                while not acquired:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                        else:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        acquired = True
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                            raise ProfileError("Could not lock the local progression profile.") from None
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise ProfileError("Progression is busy in another game. Please retry.") from None
                        time.sleep(min(0.01, remaining))
                yield
            finally:
                if acquired:
                    try:
                        stream.seek(0)
                        if os.name == "nt":
                            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
                        else:
                            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
