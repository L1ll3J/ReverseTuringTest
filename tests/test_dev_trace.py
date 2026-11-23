"""Local trace capture contracts, including real Qt job and action boundaries."""

import concurrent.futures
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Event
from time import monotonic
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from dev_trace import TraceRun, development_mode, source_fingerprint
import main as game


def events(trace):
    return [json.loads(line) for line in trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8").splitlines()]


class TraceRunTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_disabled_mode_creates_nothing_and_only_explicit_one_enables(self):
        trace = TraceRun(enabled=False, root=self.root / "absent")
        trace.start({"ignored": True})
        trace.record("ignored", {"data": object()})
        trace.snapshot({"data": object()}, reason="ignored")
        self.assertIsNone(trace.new_job("speech"))
        self.assertFalse(trace.root.exists())
        for value, expected in (("", False), ("true", False), ("0", False), ("1", True)):
            with patch.dict(os.environ, {"ALIBAI_DEV_MODE": value}):
                self.assertEqual(development_mode(), expected)

    def test_concurrent_events_are_complete_and_have_unique_sequences(self):
        trace = TraceRun(enabled=True, root=self.root)
        trace.start({"source": {"revision": "a" * 40}})
        scopes = [trace.new_job("speech", metadata={"actor": str(index)}) for index in range(8)]

        def write(scope):
            for index in range(20):
                scope.record("sample", {"index": index, "text": "Unicode: café <hello>"})

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write, scopes))
        records = events(trace)
        self.assertEqual(len(records), 169)
        self.assertEqual([record["sequence"] for record in records], list(range(1, 170)))
        samples = [record for record in records if record["kind"] == "sample"]
        self.assertEqual(len(samples), 160)
        self.assertEqual({record["job_id"] for record in samples}, {scope.job_id for scope in scopes})
        self.assertTrue(all(record["run_id"] == trace.run_id for record in records))

    def test_payloads_are_frozen_when_written_and_run_end_allows_late_results(self):
        trace = TraceRun(enabled=True, root=self.root)
        trace.start({})
        scope = trace.new_job("speech")
        state = {"characters": ["Jerry"], "private": "A private observation"}
        trace.snapshot(state, reason="before_request")
        state["characters"].append("Dave")
        trace.end("restart")
        other = TraceRun(enabled=True, root=self.root)
        other.start({})
        scope.record("request_completed", {"result": "Late reply"}, request_id="old-request")
        trace.end("closed")
        records = events(trace)
        snapshot = next(record for record in records if record["kind"] == "state_snapshot")
        self.assertEqual(snapshot["payload"]["state"]["characters"], ["Jerry"])
        self.assertEqual(sum(record["kind"] == "run_ended" for record in records), 1)
        self.assertEqual(records[-1]["run_id"], trace.run_id)
        self.assertNotEqual(trace.run_id, other.run_id)
        self.assertFalse(any(record["kind"] == "request_completed" for record in events(other)))

    def test_disk_failure_disables_once_without_exposing_exception_details(self):
        notice = Mock()
        trace = TraceRun(enabled=True, root=self.root, on_error=notice)
        trace.start({})
        with patch.object(Path, "open", side_effect=OSError("credential-sentinel")):
            trace.record("event", {"value": "record"})
            trace.record("event", {"value": "again"})
        self.assertFalse(trace.enabled)
        notice.assert_called_once_with("Development trace recording stopped. Gameplay continues.")
        self.assertNotIn("credential-sentinel", str(notice.call_args))

    def test_invalid_trace_values_disable_capture_without_stringifying_objects(self):
        secret_object = Mock()
        secret_object.__str__ = Mock(side_effect=AssertionError("Object was stringified"))
        notice = Mock()
        trace = TraceRun(enabled=True, root=self.root, on_error=notice)
        trace.start({})
        trace.record("unsupported", {"client": secret_object})
        self.assertFalse(trace.enabled)
        secret_object.__str__.assert_not_called()
        notice.assert_called_once()

    def test_existing_files_are_preserved_and_manifest_has_safe_version_metadata(self):
        previous = self.root / "older-run"
        previous.mkdir()
        previous.joinpath("keep.txt").write_text("user-owned", encoding="utf-8")
        trace = TraceRun(enabled=True, root=self.root)
        trace.start({"models": {"speech_model": "fixture"}})
        manifest = json.loads(trace.run_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], 1)
        self.assertEqual(manifest["run_id"], trace.run_id)
        self.assertIn("openai", manifest["packages"])
        self.assertEqual(previous.joinpath("keep.txt").read_text(encoding="utf-8"), "user-owned")

    def test_packaged_source_metadata_does_not_accept_arbitrary_environment_text(self):
        with patch.dict(os.environ, {"ALIBAI_BUILD_REVISION": "credential-sentinel"}), patch.object(sys, "frozen", True, create=True):
            result = source_fingerprint(self.root)
        self.assertIsNone(result["revision"])
        self.assertNotIn("credential-sentinel", json.dumps(result))

    def test_packaged_run_directory_cannot_be_inside_extraction_directory(self):
        notice = Mock()
        bundle = self.root / "bundle"
        trace = TraceRun(enabled=True, root=bundle / "dev-runs", on_error=notice)
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "_MEIPASS", str(bundle), create=True):
            trace.start({})
        self.assertFalse(trace.enabled)
        self.assertFalse(bundle.exists())
        notice.assert_called_once()


class GameTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["trace-regressions"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.environment = patch.dict(os.environ, {"ALIBAI_DEV_MODE": "", "OPENAI_API_KEY": "",
                                                   "ALIBAI_PROFILE_PATH": str(Path(self.directory.name) / "profile.json"),
                                                   "ALIBAI_SETTINGS_PATH": str(Path(self.directory.name) / "preferences.ini")})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.media = patch.object(game, "QMediaPlayer")
        self.media.start()
        self.addCleanup(self.media.stop)
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Network access in offline test"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.preparation_patch = patch.object(game.GameWindow, "maybe_prepare_next_turn")
        self.preparation_patch.start()
        self.addCleanup(self.preparation_patch.stop)
        self.window = game.GameWindow()
        self.window.trace = TraceRun(enabled=True, root=self.directory.name)
        self.window.ai_runtime.trace = self.window.trace
        self.window.show_room_allocations = False
        self.window.player_name_input.setText("Alex")
        with patch.object(game, "source_fingerprint", return_value={"revision": "a" * 40, "dirty": False}):
            self.window.start_game()
        self.client = Mock()
        self.client.api_key = "credential-sentinel"
        self.client.default_headers = {"Authorization": "Bearer credential-sentinel"}
        self.client.responses.parse.side_effect = self.reply
        self.window.ai_runtime._client = self.client

    def tearDown(self):
        for worker in list(self.window.active_workers):
            self.assertTrue(worker.wait(3000))
        self.app.processEvents()
        self.window.close_escape_menu()
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def reply(self, **request):
        schema = request["text_format"]
        context = ET.fromstring(request["input"][1]["content"])
        if "speech" in schema.model_fields:
            payload = {"addressee": None, "speech": context.get("character") + " draft"}
        elif "speaker" in schema.model_fields:
            payload = {"speaker": "Dave"}
        else:
            payload = {"target": "NONE"}
        return SimpleNamespace(status="completed", id="resp_fixture", output=[
            SimpleNamespace(type="reasoning", encrypted_content="provider-internal-sentinel"),
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text=json.dumps(payload))]),
        ], output_parsed=payload, usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15))

    def wait_for(self, condition):
        deadline = monotonic() + 3
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), "Queued trace operation did not complete")

    def test_hard_mode_records_night_assignments_and_actual_simple_kill(self):
        bare = next(weapon for weapon in self.window.weapons if weapon["id"] == "bare")
        with patch.object(self.window, "run_conversation_loop"):
            self.window.select_weapon_and_start(bare)
            self.window.item_drafts = None
            while not self.window.night_arrivals.complete:
                self.window.loiter_button.click()
            room = next(room for room, occupants in self.window.room_assignments.items() if len(occupants) == 1)
            victim = self.window.room_assignments[room][0]
            self.window.choose_room(room)
        records = events(self.window.trace)
        assigned = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "night_arrivals_started")
        self.assertEqual(len(assigned["payload"]["arrivals"]), 5)
        self.assertEqual({arrival["body_id"] for arrival in assigned["payload"]["arrivals"]},
                         {self.window.body_id(name) for name in self.window.characters})
        killed = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "kill_resolved")
        self.assertEqual((killed["payload"]["victim"], killed["payload"]["room"]), (victim, room))
        self.assertFalse(self.window.characters[victim]["alive"])
        snapshot = next(record for record in records if record["kind"] == "state_snapshot" and record["payload"]["reason"] == "kill_resolved")
        self.assertFalse(snapshot["payload"]["state"]["characters"][victim]["alive"])
        self.assertNotIn("Room Assignments", self.window.output_area.toPlainText())

    def test_manifest_fingerprints_actual_muffled_policy(self):
        manifest = json.loads(self.window.trace.run_dir.joinpath("manifest.json").read_text(encoding="utf-8"))
        policy = Path(self.window.ai_runtime.prompt_root, "prompts/muffled.xml").read_bytes()
        self.assertEqual(manifest["metadata"]["prompts"]["prompts/muffled.xml"], sha256(policy).hexdigest())

    def test_parallel_requests_record_every_draft_but_commit_only_selected_speech(self):
        self.window.phase = "conversation"
        self.window.append_speech("Alex", "Where were you?")
        for name in self.window.characters:
            self.window.remember(name, "experience", name + " private observation")
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_multiple_responses()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
        records = events(self.window.trace)
        starts = [record for record in records if record["kind"] == "request_started"]
        actor_requests = [record for record in starts if record["payload"]["task"] == "speech"]
        selector = next(record for record in starts if record["payload"]["task"] == "selector")
        self.assertEqual(len(actor_requests), 5)
        self.assertEqual(len({record["request_id"] for record in starts}), 6)
        self.assertEqual(len({record["job_id"] for record in actor_requests}), 1)
        self.assertEqual(selector["parent_job_id"], actor_requests[0]["job_id"])
        for record in actor_requests:
            context = ET.fromstring(record["payload"]["input"][1]["content"])
            experience = context.find("private_experiences/record").text
            self.assertEqual(experience, record["payload"]["actor"] + " private observation")
        selector_xml = selector["payload"]["input"][1]["content"]
        self.assertNotIn("private observation", selector_xml)
        self.assertTrue(all(name + " draft" in selector_xml for name in self.window.characters))
        committed = [record for record in records if record["kind"] == "speech_committed" and record["payload"]["speaker"] != "Alex"]
        self.assertEqual([record["payload"]["speech"] for record in committed], ["Dave draft"])
        self.assertEqual([line.text for line in self.window.public_dialogue], ["Where were you?", "Dave draft"])
        text = self.window.trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("credential-sentinel", text)
        self.assertNotIn("provider-internal-sentinel", text)

    def prepare_night(self, weapon_id, assignments):
        self.window.selected_weapon = next(weapon for weapon in self.window.weapons if weapon["id"] == weapon_id)
        plan = {self.window.body_id(name): room for room, names in assignments.items() for name in names}
        with patch.object(game, "assign_guest_rooms", return_value=plan):
            self.window.run_action_phase()
        self.window.item_drafts = None

    def test_remote_explosion_records_all_actual_deaths_and_resolved_crime_scene(self):
        self.prepare_night("c4", {"Attic": [], "Kitchen": ["Jerry", "Dave"],
                                  "Library": ["Hank"], "Basement": ["Debra"], "Cellar": ["Owen"]})
        self.window.c4_target_room = "Kitchen"
        self.window.apply_weapon_gating()
        with patch.object(self.window, "run_conversation_loop"):
            self.window.choose_room("Attic")
            self.assertIsNotNone(self.window.weapon_aftermath_panel)
            self.window.keep_weapon_button.click()
        records = events(self.window.trace)
        explosion = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "explosion_resolved")
        self.assertEqual(explosion["payload"]["victims"], ["Jerry", "Dave"])
        snapshot = next(record for record in records if record["kind"] == "state_snapshot" and record["payload"]["reason"] == "explosion_resolved")["payload"]["state"]
        self.assertEqual(snapshot["new_crime_scenes"], ["Kitchen"])
        self.assertEqual(snapshot["_override_murder_room"], "Kitchen")
        self.assertTrue(all(not snapshot["characters"][victim]["alive"] for victim in ("Jerry", "Dave")))

    def test_controlled_kill_records_controller_target_and_actual_locations(self):
        self.prepare_night("blackmagic", {"Attic": [], "Kitchen": ["Dave"],
                                          "Library": ["Jerry"], "Basement": ["Hank"], "Cellar": ["Debra", "Owen"]})
        self.window.black_magic_controller, self.window.black_magic_target = "Jerry", "Dave"
        self.window.apply_weapon_gating()
        with patch.object(self.window, "run_conversation_loop"):
            self.window.choose_room("Attic")
        records = events(self.window.trace)
        resolved = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "controlled_kill_resolved")
        self.assertEqual((resolved["payload"]["controller"], resolved["payload"]["victim"]), ("Jerry", "Dave"))
        snapshot = next(record for record in records if record["kind"] == "state_snapshot" and record["payload"]["reason"] == "controlled_kill_resolved")["payload"]["state"]
        self.assertIn("Jerry", snapshot["room_assignments"]["Kitchen"])
        self.assertNotIn("Jerry", snapshot["room_assignments"]["Library"])
        self.assertFalse(snapshot["characters"]["Dave"]["alive"])

    def test_possession_records_old_corpse_and_new_identity_without_rewriting_history(self):
        self.prepare_night("voodoo", {"Attic": [], "Kitchen": ["Dave"],
                                     "Library": ["Jerry"], "Basement": ["Hank"], "Cellar": ["Debra", "Owen"]})
        self.window.append_speech("Alex", "An earlier statement.")
        with patch.object(self.window, "run_conversation_loop"):
            self.window.choose_room("Kitchen")
        records = events(self.window.trace)
        identity = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "identity_changed")
        self.assertEqual((identity["payload"]["previous_body"], identity["payload"]["current_body"]), ("Alex", "Dave"))
        snapshot = next(record for record in records if record["kind"] == "state_snapshot" and record["payload"]["reason"] == "identity_changed")["payload"]["state"]
        self.assertEqual(snapshot["player_name"], "Dave")
        self.assertTrue(snapshot["characters"]["Alex"]["alive"])
        self.assertEqual(snapshot["room_assignments"]["Kitchen"], ["Dave"])
        kill = next(record for record in records if record["kind"] == "action_applied" and record["payload"]["action"] == "kill_resolved")
        self.assertEqual(kill["payload"]["victim"], "Alex")
        killed_snapshot = next(record for record in records if record["kind"] == "state_snapshot" and record["payload"]["reason"] == "kill_resolved")["payload"]["state"]
        self.assertEqual(killed_snapshot["player_name"], "Dave")
        self.assertFalse(killed_snapshot["characters"]["Alex"]["alive"])
        self.assertEqual(killed_snapshot["room_assignments"]["Kitchen"], ["Alex"])
        earlier = next(record for record in records if record["kind"] == "speech_committed")
        self.assertEqual(earlier["payload"]["speaker"], "Alex")

    def test_retry_reuses_job_and_frozen_payload_with_distinct_request_ids(self):
        from openai import APITimeoutError
        import httpx
        calls = []

        def reply(**request):
            calls.append(request)
            if len(calls) == 1:
                raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))
            return self.reply(**request)

        self.client.responses.parse.side_effect = reply
        self.window.phase = "conversation"
        self.window.remember("Jerry", "experience", "Original experience")
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_single_response("Jerry")
            self.wait_for(lambda: self.window.retry_action is not None and not self.window.active_workers)
            self.window.retry_ai_request()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
        starts = [record for record in events(self.window.trace) if record["kind"] == "request_started"]
        self.assertEqual(len(starts), 2)
        self.assertEqual(starts[0]["job_id"], starts[1]["job_id"])
        self.assertEqual([record["attempt"] for record in starts], [1, 2])
        self.assertNotEqual(starts[0]["request_id"], starts[1]["request_id"])
        self.assertEqual(starts[0]["payload"]["input"], starts[1]["payload"]["input"])
        self.assertNotIn("Later experience", starts[1]["payload"]["input"][1]["content"])

    def test_closed_run_keeps_late_job_result_in_old_run_without_committing_it(self):
        self.window.phase = "conversation"
        old_trace = self.window.trace
        entered, release = Event(), Event()
        success = Mock()

        def operation():
            entered.set()
            if not release.wait(2):
                raise AssertionError("Request was not released")
            return "late result"

        try:
            self.window.start_ai_request(operation, success, "Waiting")
            self.wait_for(entered.is_set)
            self.window.close()
            next_run = TraceRun(enabled=True, root=self.directory.name)
            next_run.start({})
            release.set()
            self.wait_for(lambda: not self.window.active_workers)
            success.assert_not_called()
            records = events(old_trace)
            ended = next(index for index, record in enumerate(records) if record["kind"] == "run_ended")
            ignored = next(index for index, record in enumerate(records) if record["kind"] == "job_ignored")
            self.assertGreater(ignored, ended)
            self.assertEqual(records[ignored]["run_id"], old_trace.run_id)
            self.assertFalse(any(record["kind"] == "job_ignored" for record in events(next_run)))
        finally:
            release.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
