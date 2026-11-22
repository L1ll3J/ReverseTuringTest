"""Persistent progression contracts, including real competing processes."""

from dataclasses import FrozenInstanceError
import json
import multiprocessing
import os
from pathlib import Path
from queue import Empty
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from progression import (
    ContentDefinition, ProfileError, ProfileSnapshot, ProfileStore, ProgressEvent, UnlockRule,
)


def _record_process(path, run_id, count, start, results, timeout=5.0, attempted=None):
    try:
        if not start.wait(5):
            raise AssertionError("Progression process was not released")
        store = ProfileStore(path, lock_timeout=timeout)
        if attempted is not None:
            attempted.set()
        for sequence in range(1, count + 1):
            store.record(ProgressEvent(run_id, sequence, "shared_event"))
        results.put("ok")
    except Exception as error:
        results.put((type(error).__name__, str(error)))


def _holding_reader_process(path, opened, release, results):
    store = ProfileStore(path, lock_timeout=5)
    original_read = store._read_document

    def read_with_open_handle():
        with store.path.open("r", encoding="utf-8") as stream:
            opened.set()
            if not release.wait(5):
                raise AssertionError("Profile reader was not released")
            stream.read()
        return original_read()

    store._read_document = read_with_open_handle
    try:
        store.read()
        results.put("reader-ok")
    except Exception as error:
        results.put((type(error).__name__, str(error)))


class ProgressionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "profile.json"
        self.catalogue = (
            ContentDefinition("starter_weapon", "weapon", True),
            ContentDefinition("first_weapon", "weapon", False, UnlockRule("enemy_defeated", 1)),
            ContentDefinition("later_weapon", "weapon", False, UnlockRule("enemy_defeated", 3)),
            ContentDefinition("starter_item", "item", True),
            ContentDefinition("reward_item", "item", False, UnlockRule("run_completed", 1)),
        )
        self.store = ProfileStore(self.path, self.catalogue)

    def test_read_and_default_pools_do_not_create_or_write_a_profile(self):
        nested = self.path.parent / "missing" / "profile.json"
        store = ProfileStore(nested, self.catalogue)
        self.assertEqual(store.read(), ProfileSnapshot())
        self.assertEqual(store.load(), ProfileSnapshot())
        self.assertEqual(store.available_weapons(), ("starter_weapon",))
        self.assertEqual(store.item_pool(), ("starter_item",))
        self.assertFalse(nested.parent.exists())

    def test_default_path_uses_local_app_data_without_writing_during_construction(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": self.directory.name}):
            store = ProfileStore(catalogue=self.catalogue)
        self.assertEqual(store.path, self.path.parent / "AlibAi" / "profile.json")
        self.assertFalse(store.path.parent.exists())

    def test_committed_events_unlock_in_catalogue_order_and_survive_restart(self):
        first = self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        self.assertTrue(first.applied)
        self.assertEqual(first.newly_unlocked, ("first_weapon",))
        second = self.store.record(ProgressEvent("run-a", 2, "enemy_defeated", 2))
        self.assertEqual(second.newly_unlocked, ("later_weapon",))
        reopened = ProfileStore(self.path, self.catalogue)
        self.assertEqual(reopened.read(), second.profile)
        self.assertEqual(reopened.available_weapons(), ("starter_weapon", "first_weapon", "later_weapon"))
        self.assertEqual(second.profile.counters["enemy_defeated"], 3)
        self.assertEqual(second.profile.last_sequences["run-a"], 2)

    def test_repeated_and_earlier_sequences_do_not_count_or_rewrite_again(self):
        self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        committed = self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        before = self.path.read_bytes()
        with patch("progression.os.replace", side_effect=AssertionError("Duplicate wrote a profile")):
            for sequence in (1, 2):
                result = self.store.record(ProgressEvent("run-a", sequence, "enemy_defeated", 50))
                self.assertFalse(result.applied)
                self.assertEqual(result.newly_unlocked, ())
                self.assertEqual(result.profile, committed.profile)
        self.assertEqual(self.path.read_bytes(), before)

    def test_sequence_gaps_are_rejected_without_dropping_the_missing_event(self):
        with self.assertRaises(ProfileError):
            self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        self.assertFalse(self.path.exists())
        self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        before = self.path.read_bytes()
        with self.assertRaises(ProfileError):
            self.store.record(ProgressEvent("run-a", 3, "enemy_defeated"))
        self.assertEqual(self.path.read_bytes(), before)
        self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        result = self.store.record(ProgressEvent("run-a", 3, "enemy_defeated"))
        self.assertEqual(result.profile.counters["enemy_defeated"], 3)

    def test_runs_have_independent_sequences_and_shared_progress(self):
        self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        self.store.record(ProgressEvent("run-b", 1, "enemy_defeated"))
        result = self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        self.assertEqual(result.profile.last_sequences, {"run-a": 2, "run-b": 1})
        self.assertEqual(result.profile.counters["enemy_defeated"], 3)

    def test_disabling_an_unlocked_item_changes_pool_without_losing_unlock(self):
        self.store.record(ProgressEvent("run-a", 1, "run_completed"))
        self.assertEqual(self.store.item_pool(), ("starter_item", "reward_item"))
        disabled = self.store.set_item_enabled("reward_item", False)
        self.assertIn("reward_item", disabled.unlocked)
        self.assertEqual(disabled.disabled_items, {"reward_item"})
        reopened = ProfileStore(self.path, self.catalogue)
        self.assertEqual(reopened.item_pool(), ("starter_item",))
        reopened.record(ProgressEvent("run-b", 1, "run_completed"))
        self.assertEqual(reopened.item_pool(), ("starter_item",))
        reopened.set_item_enabled("reward_item", True)
        self.assertEqual(reopened.item_pool(), ("starter_item", "reward_item"))

    def test_default_locked_unknown_and_weapon_content_cannot_be_toggled(self):
        for content_id in ("starter_item", "reward_item", "unknown_item", "starter_weapon"):
            with self.subTest(content_id=content_id), self.assertRaises(ProfileError):
                self.store.set_item_enabled(content_id, False)
        self.assertFalse(self.path.exists())

    def test_unknown_ids_counts_runs_and_fields_survive_an_update(self):
        document = {"version": 1, "counters": {"future_event": 14},
                    "unlocked": ["future_item"], "disabled_items": ["future_item"],
                    "last_sequences": {"older_run": 8}, "future_setting": {"value": [1, "opaque"]}}
        self.path.write_text(json.dumps(document), encoding="utf-8")
        result = self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        self.assertIn("future_item", result.profile.unlocked)
        self.assertIn("future_item", result.profile.disabled_items)
        self.assertEqual(result.profile.counters["future_event"], 14)
        self.assertEqual(result.profile.last_sequences["older_run"], 8)
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["future_setting"], document["future_setting"])

    def test_corrupt_or_unsupported_profiles_are_never_overwritten(self):
        valid = {"version": 1, "counters": {}, "unlocked": [], "disabled_items": [], "last_sequences": {}}
        invalid = (
            b"{broken", b"\xff", b"[]", b'{"version":1,"version":1}',
            json.dumps({**valid, "version": 2}).encode(),
            json.dumps({**valid, "version": True}).encode(),
            json.dumps({**valid, "counters": {"count": True}}).encode(),
            json.dumps({**valid, "unlocked": "not-a-list"}).encode(),
            json.dumps({**valid, "last_sequences": {"run": -1}}).encode(),
            b'{"version":1,"counters":{"bad":NaN},"unlocked":[],"disabled_items":[],"last_sequences":{}}',
        )
        for contents in invalid:
            with self.subTest(contents=contents):
                self.path.write_bytes(contents)
                for operation in (self.store.read, lambda: self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))):
                    with self.assertRaises(ProfileError):
                        operation()
                    self.assertEqual(self.path.read_bytes(), contents)

    def test_failed_atomic_replace_keeps_old_data_and_retry_sequence_available(self):
        self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        before = self.path.read_bytes()
        with patch("progression.os.replace", side_effect=OSError("simulated failure")):
            with self.assertRaises(ProfileError):
                self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".profile.json.*.tmp")), [])
        result = self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
        self.assertTrue(result.applied)
        self.assertEqual(result.profile.counters["enemy_defeated"], 2)

    def test_fsync_failure_does_not_replace_the_committed_profile(self):
        self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        before = self.path.read_bytes()
        with patch("progression.os.fsync", side_effect=OSError("simulated flush failure")), patch(
            "progression.os.replace"
        ) as replace:
            with self.assertRaises(ProfileError):
                self.store.record(ProgressEvent("run-a", 2, "enemy_defeated"))
            replace.assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(list(self.path.parent.glob(".profile.json.*.tmp")), [])

    def test_write_flushes_a_sibling_temporary_file_before_atomic_replacement(self):
        steps = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(descriptor):
            steps.append("fsync")
            return real_fsync(descriptor)

        def replace(source, target):
            self.assertEqual(Path(source).parent, self.path.parent)
            self.assertEqual(Path(target), self.path)
            self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8"))["version"], 1)
            steps.append("replace")
            return real_replace(source, target)

        with patch("progression.os.fsync", side_effect=fsync), patch("progression.os.replace", side_effect=replace):
            self.store.record(ProgressEvent("run-a", 1, "enemy_defeated"))
        self.assertEqual(steps, ["fsync", "replace"])

    def test_snapshots_copy_their_inputs_and_remain_immutable(self):
        counts, unlocked, disabled, sequences = {"count": 2}, {"a"}, {"a"}, {"run": 1}
        snapshot = ProfileSnapshot(counts, unlocked, disabled, sequences)
        counts["count"] = 99
        unlocked.clear()
        disabled.clear()
        sequences["run"] = 50
        self.assertEqual(snapshot.counters, {"count": 2})
        self.assertEqual(snapshot.unlocked, {"a"})
        self.assertEqual(snapshot.disabled_items, {"a"})
        self.assertEqual(snapshot.last_sequences, {"run": 1})
        with self.assertRaises(TypeError):
            snapshot.counters["count"] = 3
        with self.assertRaises(FrozenInstanceError):
            snapshot.unlocked = frozenset()

    def test_input_contracts_reject_invalid_counts_and_duplicate_content_ids(self):
        for value in (0, -1, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                ProgressEvent("run", value, "event")
            with self.subTest(value=value), self.assertRaises(ValueError):
                UnlockRule("event", value)
        with self.assertRaises(ValueError):
            ProfileStore(self.path, (self.catalogue[0], self.catalogue[0]))
        for timeout in (-1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                ProfileStore(self.path, lock_timeout=timeout)

    def test_read_does_not_reconcile_new_catalogue_rules_or_write_unlocks(self):
        ProfileStore(self.path).record(ProgressEvent("run-a", 1, "enemy_defeated", 3))
        before = self.path.read_bytes()
        self.assertEqual(self.store.read().unlocked, set())
        self.assertEqual(self.store.available_weapons(), ("starter_weapon",))
        self.assertEqual(self.path.read_bytes(), before)
        update = self.store.record(ProgressEvent("run-a", 2, "other_event"))
        self.assertEqual(update.newly_unlocked, ("first_weapon", "later_weapon"))

    def run_processes(self, run_ids, count):
        context = multiprocessing.get_context("spawn")
        start, results = context.Event(), context.Queue()
        processes = [context.Process(target=_record_process, args=(str(self.path), run_id, count, start, results))
                     for run_id in run_ids]
        try:
            for process in processes:
                process.start()
            start.set()
            messages = [results.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(messages, ["ok"] * len(processes))
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_actual_process_contention_does_not_lose_committed_events(self):
        self.run_processes(["run-a", "run-b", "run-c", "run-d"], 20)
        profile = self.store.read()
        self.assertEqual(profile.counters["shared_event"], 80)
        self.assertEqual(profile.last_sequences, {name: 20 for name in ("run-a", "run-b", "run-c", "run-d")})

    def test_actual_processes_cannot_double_count_the_same_committed_event(self):
        self.run_processes(["same-run"] * 4, 1)
        self.assertEqual(self.store.read().counters["shared_event"], 1)

    def test_writer_waits_for_an_actual_reader_before_replacing_its_open_profile(self):
        self.store.record(ProgressEvent("initial-run", 1, "shared_event"))
        context = multiprocessing.get_context("spawn")
        opened, release, start, attempted = (context.Event() for _ in range(4))
        results = context.Queue()
        reader = context.Process(target=_holding_reader_process,
                                 args=(str(self.path), opened, release, results))
        writer = context.Process(target=_record_process,
                                 args=(str(self.path), "writer-run", 1, start, results, 5, attempted))
        processes = (reader, writer)
        try:
            reader.start()
            self.assertTrue(opened.wait(5), "Reader did not open the profile")
            writer.start()
            start.set()
            self.assertTrue(attempted.wait(5), "Writer did not attempt its update")
            with self.assertRaises(Empty):
                results.get(timeout=0.1)
            release.set()
            messages = [results.get(timeout=10) for _ in processes]
            self.assertCountEqual(messages, ["reader-ok", "ok"])
            for process in processes:
                process.join(timeout=5)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(self.store.read().counters["shared_event"], 2)
        finally:
            release.set()
            start.set()
            for process in processes:
                if process.pid is not None:
                    if process.is_alive():
                        process.terminate()
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_actual_process_lock_timeout_preserves_profile_and_recovers(self):
        context = multiprocessing.get_context("spawn")
        start, results = context.Event(), context.Queue()
        process = context.Process(target=_record_process,
                                  args=(str(self.path), "blocked-run", 1, start, results, 0.1))
        try:
            with self.store._locked():
                process.start()
                start.set()
                result = results.get(timeout=10)
                self.assertEqual(result[0], "ProfileError")
                self.assertIn("busy", result[1])
                self.assertFalse(self.path.exists())
            process.join(timeout=5)
            self.assertEqual(process.exitcode, 0)
            self.assertTrue(self.store.record(ProgressEvent("blocked-run", 1, "shared_event")).applied)
        finally:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
            results.close()
            results.join_thread()


if __name__ == "__main__":
    unittest.main()
