"""Offline real-Qt checks for Love effects, phase hooks and context snapshots."""

import contextlib
from dataclasses import replace
from hashlib import sha256
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", str(Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication

import main as game
from ai_context import FeelingKind, render_character_context
from dev_trace import TraceRun, prompt_fingerprints
from game_rules import BodyState, ItemRuleError, ItemUseContext, PlayerItemRules
from items import ITEM_CATALOGUE, Inventory, eligible_item_pool
from love_potion import PAST_HANGOVER, PAST_INFATUATION


class LoveItemRuleTests(unittest.TestCase):
    def test_default_single_use_is_mechanical_and_freezes_giver_identity(self):
        inventory = Inventory("owner")
        rules = PlayerItemRules("owner", inventory)
        player = BodyState("original-body", "Alex", True, False)
        target = BodyState("target", "Dave", True, True)
        context = ItemUseContext(1, "conversation", False, "owner", player.body_id, (player, target))
        self.assertIn("love_potion", eligible_item_pool())
        self.assertFalse(ITEM_CATALOGUE["love_potion"].reusable)
        entry = inventory.grant("love_potion")
        for invalid in (replace(context, phase="action"), replace(context, ai_busy=True),
                        replace(context, bodies=(player, replace(target, alive=False)))):
            with self.assertRaises(ItemRuleError):
                rules.use_item(entry.instance_id, invalid, target.body_id)
            self.assertIn(entry, inventory.entries)
        events = rules.use_item(entry.instance_id, context, target.body_id, activation_id="first-use")
        effect = events[0]
        self.assertEqual((effect.event_type, effect.giver_label, effect.subject_body_id, effect.target_body_id),
                         ("love_potion_applied", "Alex", "original-body", "target"))
        self.assertEqual((effect.public_text, effect.private_text, effect.disposition_text), (None, None, None))
        self.assertNotIn(entry, inventory.entries)
        self.assertEqual(rules.use_item(entry.instance_id, context, target.body_id, activation_id="first-use"), ())
        next_entry = inventory.grant("love_potion")
        self.assertEqual(rules.use_item(next_entry.instance_id, context, target.body_id)[0].event_type,
                         "love_potion_applied")


class LovePotionIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["love-potion-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.directory = Path(directory)
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(self.directory / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(self.directory / "settings.ini"), "ALIBAI_DEV_MODE": "0",
        }))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.stack.enter_context(patch.object(game.GameWindow, "run_conversation_loop"))
        self.window = game.GameWindow()
        self.window.player_name_input.setText("Alex")
        self.window.start_game()
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.advance_love_histories(discussion=True)

    def tearDown(self):
        for worker in list(self.window.active_workers):
            self.assertTrue(worker.wait(3000))
        self.window.overlay_host.close_page()
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def apply(self, name="Dave"):
        entry = self.window.inventory.grant("love_potion")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id(name)))

    def test_use_changes_snapshot_without_altering_testimony_or_public_knowledge(self):
        self.window.remember("Dave", "experience", "You witnessed Alex kill Jerry.")
        before = self.window.build_character_context("Dave")
        revision = self.window.conversation_revision
        self.apply()
        after = self.window.build_character_context("Dave")
        self.assertGreater(self.window.conversation_revision, revision)
        self.assertEqual(after.experiences, before.experiences)
        self.assertEqual(after.public_events, before.public_events)
        self.assertEqual(after.current_feelings[0].kind, FeelingKind.INFATUATION)
        self.assertNotEqual(after, before)
        self.assertEqual(self.window.build_character_context("Hank").current_feelings, ())
        self.assertEqual(before.current_feelings, ())
        npc_xml = render_character_context(after)
        self.assertNotIn("love_potion", npc_xml)
        self.assertNotIn("dose_id", npc_xml)

    def test_real_night_and_discussion_hooks_archive_then_activate_then_archive(self):
        self.apply()
        self.window.run_action_phase()
        night = self.window.build_character_context("Dave")
        self.assertEqual(night.current_feelings, ())
        self.assertEqual(night.experiences[-1].round_number, 1)
        self.assertEqual(night.experiences[-1].text, PAST_INFATUATION.format(giver="Alex"))
        self.window.run_conversation_phase()
        day = self.window.build_character_context("Dave")
        self.assertEqual(day.current_feelings[0].kind, FeelingKind.BITTERNESS)
        unchanged = self.window.characters["Dave"]["love_history"]
        self.window.run_conversation_phase()
        self.assertIs(self.window.characters["Dave"]["love_history"], unchanged)
        self.window.run_action_phase()
        final = self.window.build_character_context("Dave")
        self.assertEqual(final.current_feelings, ())
        self.assertEqual(final.experiences[-1].text, PAST_HANGOVER.format(giver="Alex"))
        self.assertEqual(final.experiences[-1].round_number, 2)

    def test_death_before_next_discussion_does_not_create_a_hangover(self):
        self.apply()
        self.window.run_action_phase()
        self.window.resolve_death("Dave", "night-death")
        self.window.run_conversation_phase()
        history = self.window.characters["Dave"]["love_history"]
        self.assertEqual(len(history.experiences.stages), 1)
        self.assertIsNone(history.pending)

    def test_game_ending_before_next_discussion_does_not_create_a_hangover(self):
        self.apply()
        self.window.run_action_phase()
        with patch.object(self.window, "check_victory", return_value=True):
            self.window.run_conversation_phase()
        self.assertEqual(len(self.window.characters["Dave"]["love_history"].experiences.stages), 1)

    def test_newest_dose_during_both_stages_preserves_only_actual_experiences(self):
        self.apply()
        self.apply()
        current = self.window.build_character_context("Dave")
        self.assertEqual(len(current.experiences), 1)
        self.assertEqual(len(current.current_feelings), 1)
        self.window.run_action_phase()
        self.window.run_conversation_phase()
        self.apply()
        replaced = self.window.build_character_context("Dave")
        self.assertEqual(len(replaced.experiences), 3)
        self.assertEqual(replaced.experiences[-1].text, PAST_HANGOVER.format(giver="Alex"))
        self.assertEqual(replaced.current_feelings[0].kind, FeelingKind.INFATUATION)
        self.assertEqual(len(replaced.current_feelings), 1)

    def test_replaying_effect_events_after_expiry_never_duplicates_or_rewinds(self):
        entry = self.window.inventory.grant("love_potion")
        context = ItemUseContext(1, "conversation", False, self.window.player_owner_id,
                                 self.window.body_id("Alex"), self.window.body_states())
        events = self.window.item_rules.use_item(entry.instance_id, context, self.window.body_id("Dave"))
        self.window.apply_item_events(events)
        self.window.run_action_phase()
        self.window.run_conversation_phase()
        before = self.window.build_character_context("Dave")
        items_used = self.window.items_used
        revision = self.window.conversation_revision
        self.window.apply_item_events(events)
        self.assertEqual(self.window.build_character_context("Dave"), before)
        self.assertEqual(self.window.items_used, items_used)
        self.assertEqual(self.window.conversation_revision, revision)

    def test_voodoo_changes_player_body_without_renaming_captured_giver(self):
        self.apply()
        self.window.room_assignments = {"Cellar": ["Owen"]}
        self.window.new_crime_scenes = []
        self.window.commit_attack("Owen", "Cellar", weapon_id="voodoo")
        self.assertEqual(self.window.player_name, "Owen")
        self.assertEqual(self.window.build_character_context("Dave").current_feelings[0].giver, "Alex")
        self.window.round_number = 2
        self.window.advance_love_histories(discussion=False)
        self.window.advance_love_histories(discussion=True)
        self.assertEqual(self.window.build_character_context("Dave").current_feelings[0].giver, "Alex")

    def test_private_history_is_portable_with_its_experiencing_npc_data(self):
        self.apply()
        data = self.window.characters.pop("Dave")
        self.window.characters["AnotherBody"] = data
        self.assertEqual(self.window.build_character_context("AnotherBody").current_feelings[0].giver, "Alex")
        self.assertNotIn("love_history", self.window.characters.get("Dave", {}))

    def test_trace_can_serialize_owned_history_and_fingerprints_active_templates(self):
        trace = TraceRun(enabled=True, root=self.directory / "traces")
        trace.start({})
        self.window.trace = trace
        self.apply()
        self.window.trace_state("love_applied")
        self.assertTrue(trace.enabled)
        snapshots = [json.loads(line) for line in trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8").splitlines()]
        snapshot = next(entry for entry in snapshots if entry["kind"] == "state_snapshot")
        stage = snapshot["payload"]["state"]["characters"]["Dave"]["love_history"]["experiences"]["stages"][0]
        self.assertEqual(stage["current_feeling"], {"kind": "infatuation", "giver": "Alex"})
        root = Path(__file__).resolve().parents[1]
        fingerprints = prompt_fingerprints(root)
        for filename in ("current_infatuation.xml", "current_bitterness.xml"):
            relative = "prompts/" + filename
            self.assertEqual(fingerprints[relative], sha256(root.joinpath(relative).read_bytes()).hexdigest())


if __name__ == "__main__":
    unittest.main()
