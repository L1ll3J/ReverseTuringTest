"""Offline integration checks for real room offers, item effects and progression."""

import contextlib
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
from time import monotonic
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtTest import QTest
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtWidgets import QApplication, QLabel, QTextEdit

import main as game
from content import CONTENT_CATALOGUE
from dev_trace import TraceRun
from game_rules import MUFFLED_SPEECH, MUFFLE_EXPIRED, BANANA_DISPOSITION
from items import DraftState
from progression import ProfileError, ProfileStore, ProgressEvent


class ItemIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["item-integration"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.path = Path(self.temp.name) / "profile.json"
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(self.path), "ALIBAI_DEV_MODE": "0",
            "ALIBAI_SETTINGS_PATH": str(Path(self.temp.name) / "preferences.ini"),
        }))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.speech = self.stack.enter_context(patch.object(game.AIRuntime, "speech", return_value=game.SpeechProposal(None, "My account.")))
        self.stack.enter_context(patch.object(game.AIRuntime, "select_reply", side_effect=lambda state, events, dialogue, proposals: next(iter(proposals))))
        self.stack.enter_context(patch.object(game.AIRuntime, "vote", return_value="NONE"))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.window = game.GameWindow()
        self.window.player_name_input.setText("Alex")
        self.window.start_game()
        self.stack.enter_context(patch.object(self.window, "run_conversation_loop"))

    def tearDown(self):
        for worker in list(self.window.active_workers):
            worker.wait(3000)
        self.app.processEvents()
        if self.window.draft_dialog:
            self.window.draft_dialog.resolve()
        if self.window.inventory_dialog:
            self.window.inventory_dialog.close()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def weapon(self, item_id="bare"):
        return next(weapon for weapon in self.window.weapons if weapon["id"] == item_id)

    def night(self, weapon_id="bare", assignments=None):
        self.window.select_weapon_and_start(self.weapon())
        self.window.selected_weapon = self.weapon(weapon_id)
        self.window.room_assignments = assignments or {
            "Attic": ["Dave", "Jerry"], "Kitchen": ["Hank", "Owen"],
            "Library": ["Debra"], "Basement": [], "Cellar": [],
        }

    def settle_offer(self, item_id=None):
        offer = self.window.item_drafts.pending_offer
        self.assertIsNotNone(offer)
        if item_id is None:
            self.window.skip_item_offer(offer.offer_id)
        else:
            self.window.choose_item_offer(offer.offer_id, offer.options.index(item_id))

    def conversation(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.update_inventory_button()

    def give(self, item_id, target=None):
        entry = self.window.inventory.grant(item_id)
        target_id = self.window.body_id(target) if target else None
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, target_id))
        return entry

    def vote(self, ballots):
        self.window.phase = "conversation"
        if self.window.player_reply_required():
            self.window.interject_input.setText("My account.")
            self.window.send_interjection()
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.window.player_vote(ballots[self.window.player_name])
        for voter, target in ballots.items():
            if voter != self.window.player_name:
                self.window.collect_ai_vote(voter, target)

    def wait_for_workers(self):
        deadline = monotonic() + 3
        while self.window.active_workers and monotonic() < deadline:
            QTest.qWait(5)
        self.assertFalse(self.window.active_workers)

    def test_offer_inventory_and_unlock_goals_resolve_readable_body_fonts(self):
        goals = [label for label in self.window.weapon_picker_widget.findChildren(QLabel)
                 if "\n\nLocked:" in label.text()]
        self.assertTrue(goals)
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family for label in goals))
        for weapon in self.window.weapons:
            if weapon["id"] != "bare":
                self.assertTrue(any(label.text().startswith(weapon["desc"]) for label in goals))
        self.night()
        self.window.choose_room("Basement")
        self.app.processEvents()
        dialog = self.window.draft_dialog
        labels = dialog.findChildren(QLabel)
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family for label in labels))
        self.assertTrue(labels[1].wordWrap())
        for label in labels:
            if label.wordWrap():
                self.assertGreaterEqual(label.height(), label.heightForWidth(label.width()))
        self.settle_offer("banana")
        self.conversation()
        self.window.open_inventory()
        self.app.processEvents()
        self.assertTrue(all(QFontInfo(label.font()).family() != self.window.pixel_font_family
                            for label in self.window.inventory_dialog.findChildren(QLabel)))

    def test_how_to_play_covers_current_items_progression_and_evidence(self):
        guide = game.HowToPlayWindow(self.window.pixel_font_family, self.window)
        text = guide.findChild(QTextEdit).toPlainText()
        for feature in ("three items", "Inventory", "Muffle", "Spare Paper", "Guardian Angel", "next run", "photograph"):
            self.assertIn(feature, text)
        self.assertNotIn("hallucinat", text.lower())
        guide.close()

    def test_production_catalogue_uses_approved_unlocks_and_bare_is_initial_weapon(self):
        self.assertEqual(self.window.profile_store.available_weapons(), ("bare",))
        self.assertEqual(self.window.profile_store.item_pool(), ("banana", "muffle", "spare_paper"))
        goals = {content.content_id: content.unlock_rule for content in CONTENT_CATALOGUE}
        self.assertEqual(goals["knife"], game.CONTENT_CATALOGUE[1].unlock_rule)
        self.window.select_weapon_and_start(self.weapon("knife"))
        self.assertEqual(self.window.phase, "weapon_select")
        self.assertIsNone(self.window.selected_weapon)

    def test_room_offer_blocks_action_until_resolved_and_stale_clicks_cannot_reenter(self):
        self.night()
        self.window.choose_room("Library")
        offer = self.window.item_drafts.pending_offer
        self.assertTrue(self.window.characters["Debra"]["alive"])
        self.assertEqual(len(set(offer.options)), 3)
        self.window.choose_room("Attic")
        self.assertEqual(self.window.item_drafts.pending_offer, offer)
        self.settle_offer("banana")
        self.assertFalse(self.window.characters["Debra"]["alive"])
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.window.choose_item_offer(offer.offer_id, 0)
        self.assertEqual(len(self.window.inventory.entries), 1)

    def test_closing_offer_skips_permanently_and_continues_accepted_action(self):
        self.night()
        self.window.choose_room("Basement")
        self.window.draft_dialog.reject()
        self.assertIsNone(self.window.item_drafts.pending_offer)
        self.assertEqual(self.window.rooms_visited, ["Basement"])
        self.assertIsNone(self.window.item_drafts.enter_room("Basement"))
        self.assertFalse(self.window.inventory.entries)

    def test_offer_reroll_invalidates_old_buttons_and_only_spends_with_alternative(self):
        self.night()
        self.window.choose_room("Basement")
        offer = self.window.item_drafts.pending_offer
        self.window.reroll_item_offer(offer.offer_id, 0)
        self.assertEqual(self.window.item_drafts.rerolls_remaining, 1)
        self.assertEqual(self.window.item_drafts.pending_offer, offer)
        self.settle_offer()
        self.window.item_drafts = DraftState("player", tuple(game.ITEM_CATALOGUE))
        self.window.offer_room_item("Cellar", Mock())
        old = self.window.item_drafts.pending_offer
        self.window.reroll_item_offer(old.offer_id, 1)
        updated = self.window.item_drafts.pending_offer
        self.assertNotEqual(updated.offer_id, old.offer_id)
        self.assertEqual(self.window.item_drafts.rerolls_remaining, 0)
        self.window.choose_item_offer(old.offer_id, 1)
        self.assertFalse(self.window.inventory.entries)
        self.settle_offer()

    def test_inventory_preserves_composing_draft_when_used(self):
        self.night()
        self.window.choose_room("Library")
        self.settle_offer("banana")
        self.window.interject_input.setText("A half-written alibi")
        entry = self.window.inventory.entries[0]
        self.window.open_inventory()
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertEqual(self.window.interject_input.text(), "A half-written alibi")
        self.assertEqual(self.window.items_used, 1)
        self.assertIn("Alex gave Dave a banana.", [record.text for record in self.window.public_events])
        self.assertEqual(self.window.characters["Dave"]["dispositions"][-1].text,
                         BANANA_DISPOSITION.format(giver="Alex", day=1))

    def test_item_use_rejects_busy_dead_targets_and_invalidates_failed_retry(self):
        self.conversation()
        entry = self.window.inventory.grant("banana")
        self.window.ai_busy = True
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertEqual(len(self.window.inventory.entries), 1)
        self.window.ai_busy = False
        self.window.characters["Dave"]["alive"] = False
        self.assertFalse(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.window.characters["Dave"]["alive"] = True
        retry = Mock()
        self.window.retry_action = retry
        serial = self.window.request_serial
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.assertGreater(self.window.request_serial, serial)
        self.assertIsNone(self.window.retry_action)
        self.window.retry_ai_request()
        retry.assert_not_called()

    def test_muffle_prevents_generation_selector_sees_override_and_commit_rechecks(self):
        self.conversation()
        self.give("muffle", "Dave")
        seen = []
        with patch.object(self.window.ai_runtime, "select_reply", side_effect=lambda state, events, dialogue, proposals:
                          seen.append(dict(proposals)) or "Dave"):
            self.window.run_multiple_responses()
            self.wait_for_workers()
        self.assertNotIn("Dave", [call.args[0].character for call in self.speech.call_args_list])
        self.assertEqual(seen[0]["Dave"].speech, MUFFLED_SPEECH)
        self.assertEqual(self.window.public_dialogue[-1].text, MUFFLED_SPEECH)
        self.window.handle_best_response(game.SpeechProposal("Alex", "Forbidden articulate text"), "Dave")
        self.assertEqual(self.window.public_dialogue[-1].text, MUFFLED_SPEECH)
        self.assertIsNone(self.window.public_dialogue[-1].addressee)
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertIn("Dave", self.window.expected_voters)

    def test_muffle_expires_next_night_with_memory(self):
        self.conversation()
        self.give("muffle", "Dave")
        self.window.run_action_phase()
        # The first real night starts at round 2 from this round-1 decision point.
        self.assertEqual(self.window.round_number, 2)
        self.assertIsNone(self.window.item_rules.speech_override(self.window.body_id("Dave")))
        self.assertEqual(self.window.characters["Dave"]["experiences"][-1].text, MUFFLE_EXPIRED)

    def test_spare_paper_stacks_weights_freeze_and_resolve_once(self):
        self.conversation()
        self.give("spare_paper")
        self.give("spare_paper")
        ballots = {"Alex": "Dave", "Dave": "Alex", "Jerry": "Alex", "Owen": "Alex", "Hank": "Alex", "Debra": "NONE"}
        self.vote(ballots)
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.player_alive)
        self.assertEqual(self.window.item_rules.pending_ballot_bonus, 0)
        records = [record.text for record in self.window.public_events]
        self.assertEqual(records.count("Alex's vote for Dave counted as 5 votes."), 1)
        count = self.window.profile.counters["survived_vote"]
        self.window.tally_votes()
        self.assertEqual(self.window.profile.counters["survived_vote"], count)
        self.assertEqual(self.window.innocents_executed, 1)

    def test_guardian_prevents_execution_without_false_death_or_innocence_then_consumes(self):
        self.conversation()
        self.give("guardian_angel")
        duplicate = self.window.inventory.grant("guardian_angel")
        self.assertFalse(self.window.use_inventory_item(duplicate.instance_id))
        self.assertIn(duplicate, self.window.inventory.entries)
        ballots = {name: "Alex" for name in self.window.characters}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.game_over)
        self.assertFalse(self.window.item_rules.protection_armed)
        self.assertEqual(self.window.innocents_executed, 0)
        self.assertEqual(self.window.profile.counters["survived_vote"], 1)
        self.assertIn("Against all expectation, Alex survives.", [event.text for event in self.window.public_events])
        self.vote(ballots)
        self.assertFalse(self.window.player_alive)
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.profile.counters["survived_vote"], 1)

    def test_knife_attacks_two_actual_rooms_and_rewards_second_physical_entry(self):
        self.night("knife")
        self.window.knife_extra_target = "Hank"
        self.window.knife_used_bloodlust = True
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertFalse(self.window.characters["Dave"]["alive"])
        self.assertTrue(self.window.characters["Hank"]["alive"])
        self.assertEqual(self.window.item_drafts.pending_offer.room_id, "Kitchen")
        self.settle_offer("muffle")
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.assertEqual([(attack["target_label"], attack["room"]) for attack in self.window.night_attacks],
                         [("Dave", "Attic"), ("Hank", "Kitchen")])
        self.assertEqual(len(self.window.characters["Jerry"]["experiences"]), 1)
        self.assertIn("Alex kill Dave in the Attic", self.window.characters["Jerry"]["experiences"][0].text)
        self.assertEqual(len(self.window.characters["Owen"]["experiences"]), 1)
        self.assertIn("Alex kill Hank in the Kitchen", self.window.characters["Owen"]["experiences"][0].text)
        self.assertEqual(self.window.profile.counters["player_kill"], 2)
        self.assertEqual(self.window.item_drafts.seen_rooms, {"Attic", "Kitchen"})
        self.assertEqual(len(self.window.inventory.entries), 1)

    def test_knife_does_not_kill_primary_twice(self):
        self.night("knife")
        self.window.knife_extra_target = "Debra"
        self.window.choose_room("Library")
        self.settle_offer()
        self.assertEqual(len(self.window.night_attacks), 1)
        self.assertEqual(self.window.profile.counters["player_kill"], 1)

    def test_remote_blast_does_not_offer_reward_for_target_room(self):
        self.night("c4")
        self.window.c4_target_room = "Kitchen"
        self.window.choose_room("Basement")
        self.settle_offer()
        self.assertEqual(self.window.item_drafts.seen_rooms, {"Basement"})
        self.assertEqual({attack["target_label"] for attack in self.window.night_attacks}, {"Hank", "Owen"})
        self.assertFalse(self.window.characters["Hank"]["alive"])
        self.assertFalse(self.window.characters["Owen"]["alive"])

    def test_voodoo_preserves_body_affinity_history_guardian_and_truthful_witness(self):
        self.conversation()
        self.give("banana", "Jerry")
        self.give("guardian_angel")
        original_player_id = self.window.body_id("Alex")
        target_id = self.window.body_id("Dave")
        self.window.append_speech("Alex", "My old statement")
        self.window.phase = "weapon_select"
        self.night("voodoo")
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertEqual(self.window.player_name, "Dave")
        self.assertEqual(self.window.body_id(self.window.player_name), target_id)
        self.assertFalse(self.window.characters["Alex"]["alive"])
        self.assertTrue(self.window.item_rules.protection_armed)
        self.assertEqual(self.window.item_rules.affinities[0].subject_body_id, original_player_id)
        self.assertEqual(self.window.public_dialogue[0].speaker, "Alex")
        record = self.window.characters["Jerry"]["experiences"][-1].text
        self.assertEqual(record, "You (Jerry) were in the Attic. You saw Alex stab Dave. Afterwards, Alex's body was lying dead there, and Dave was alive.")
        self.assertTrue(self.window.resolve_death("Dave", "test-new-body").prevented)
        self.assertTrue(self.window.player_alive)
        self.assertFalse(self.window.item_rules.protection_armed)

    def test_unlocks_saved_from_actions_only_and_frozen_until_next_run(self):
        self.night()
        original_pool = self.window.run_item_pool
        self.window.append_speech("Dave", "I killed three people and survived a vote.")
        self.assertFalse(self.path.exists())
        self.window.choose_room("Library")
        self.settle_offer()
        self.assertIn("knife", self.window.profile.unlocked)
        self.assertNotIn("knife", self.window.run_weapons)
        self.assertEqual(self.window.run_item_pool, original_pool)
        self.assertEqual(self.window.profile_store.available_weapons(), ("bare", "knife"))

    def test_save_failure_preserves_order_and_retries_same_sequences_without_double_progress(self):
        with patch.object(self.window.profile_store, "record", side_effect=ProfileError("Disk unavailable")):
            self.window.queue_progress("player_kill")
            self.window.queue_progress("banana_given")
        pending = tuple(self.window.pending_progress)
        self.assertEqual([event.sequence for event in pending], [1, 2])
        self.assertTrue(self.window.progress_retry_button.isVisible())
        self.window.retry_progress()
        profile = self.window.profile_store.read()
        self.assertEqual(dict(profile.counters), {"player_kill": 1, "banana_given": 1})
        self.assertEqual(profile.last_sequences[self.window.run_id], 2)
        self.assertFalse(self.window.pending_progress)
        self.window.retry_progress()
        self.assertEqual(dict(self.window.profile_store.read().counters), dict(profile.counters))

    def test_three_actual_kills_unlock_c4_once_and_save_retry_does_not_recount(self):
        self.night()
        self.assertTrue(self.window.commit_attack("Dave", "Attic"))
        self.assertFalse(self.window.commit_attack("Dave", "Attic"))
        self.assertTrue(self.window.commit_attack("Jerry", "Attic"))
        self.assertEqual(self.window.run_player_kills, 2)
        self.assertNotIn("c4", self.window.profile.unlocked)
        with patch.object(self.window.profile_store, "record", side_effect=ProfileError("Disk unavailable")):
            self.assertTrue(self.window.commit_attack("Hank", "Kitchen"))
        self.assertEqual([event.event_type for event in self.window.pending_progress],
                         ["player_kill", "three_kills_one_run"])
        self.window.retry_progress()
        self.window.retry_progress()
        self.assertEqual(self.window.run_player_kills, 3)
        self.assertEqual(self.window.profile.counters["player_kill"], 3)
        self.assertEqual(self.window.profile.counters["three_kills_one_run"], 1)
        self.assertIn("c4", self.window.profile.unlocked)
        self.assertNotIn("c4", self.window.run_weapons)

    def test_execution_and_prevented_death_do_not_count_towards_c4(self):
        self.conversation()
        self.give("guardian_angel")
        self.assertTrue(self.window.resolve_death("Alex", "prevented").prevented)
        self.assertTrue(self.window.resolve_death("Dave", "execution").killed)
        self.assertEqual(self.window.run_player_kills, 0)
        self.assertNotIn("three_kills_one_run", self.window.profile.counters)

    def test_corrupt_profile_is_never_overwritten_or_silently_reset(self):
        self.path.write_text("not a profile", encoding="utf-8")
        self.assertFalse(self.window.load_profile())
        self.window.queue_progress("player_kill")
        self.assertEqual(self.path.read_text(encoding="utf-8"), "not a profile")
        self.assertEqual(self.window.pending_progress[0].sequence, 1)
        self.assertIn("invalid", self.window.progress_notice.text())
        self.assertEqual(self.window.profile_store.path, self.path)

    def test_witness_unlock_uses_living_body_ids_not_dialogue_claims(self):
        self.night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertIn(self.window.body_id("Jerry"), self.window.witness_body_ids)
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertIn("stopwatch", self.window.profile.unlocked)
        self.assertEqual(self.window.profile.counters["survived_witnessed_vote"], 1)

    def test_unused_inventory_is_retained_across_multiple_nights(self):
        self.night()
        self.window.choose_room("Library")
        self.settle_offer("muffle")
        entry = self.window.inventory.entries[0]
        self.window.run_action_phase()
        self.assertIn(entry, self.window.inventory.entries)
        self.assertEqual(self.window.round_number, 2)
        self.assertIsNone(self.window.item_drafts.enter_room("Library"))

    def test_dead_witness_does_not_unlock_stopwatch(self):
        self.night()
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertIn(self.window.body_id("Jerry"), self.window.witness_body_ids)
        self.window.resolve_death("Jerry", "test-witness-death")
        ballots = {name: "NONE" for name, data in self.window.characters.items() if data["alive"]}
        ballots["Alex"] = "NONE"
        self.vote(ballots)
        self.assertNotIn("stopwatch", self.window.profile.unlocked)
        self.assertNotIn("survived_witnessed_vote", self.window.profile.counters)

    def test_item_effects_and_frozen_vote_weights_have_serializable_developer_snapshots(self):
        trace = TraceRun(enabled=True, root=Path(self.temp.name) / "traces")
        self.window.trace = trace
        self.window.ai_runtime.trace = trace
        trace.start({"gameplay_run_id": self.window.run_id})
        self.conversation()
        self.give("muffle", "Dave")
        self.give("spare_paper")
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertTrue(trace.enabled)
        records = [json.loads(line) for line in (trace.run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        state = [record["payload"]["state"] for record in records if record["kind"] == "state_snapshot"][-1]
        self.assertEqual(state["player_items"]["vote_weights"][self.window.body_id("Alex")], 3)
        self.assertEqual(state["player_items"]["muffles"][0]["target_body_id"], self.window.body_id("Dave"))
        self.assertEqual(state["player_items"]["run_id"], self.window.run_id)

    def test_using_any_item_blocks_the_bare_hands_no_item_victory_unlock(self):
        self.conversation()
        self.give("banana", "Jerry")
        self.window.phase = "weapon_select"
        self.night()
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.game_over)
        self.assertNotIn("voodoo", self.window.profile.unlocked)
        self.assertNotIn("bare_hands_no_item_victory", self.window.profile.counters)

    def test_final_attack_publishes_camera_before_awarding_bare_no_items_victory(self):
        self.night()
        for name in ("Hank", "Owen", "Debra"):
            self.window.characters[name]["alive"] = False
        self.window.npc_items = game.NPCItemState({"Dave": "camera"})
        self.window.npc_items.capture_night(1, self.window.room_assignments, ("Dave", "Jerry"))
        self.window.choose_room("Attic")
        self.settle_offer()
        self.window.kill_victim("Dave")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.profile.counters["bare_hands_no_item_victory"], 1)
        self.assertTrue(any(record.source == "Photograph" for record in self.window.public_events))


if __name__ == "__main__":
    unittest.main()
