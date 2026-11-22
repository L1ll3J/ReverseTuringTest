"""Offline regression checks using the real Qt widgets and worker signals.

Run with: python -m unittest discover -s tests -v
"""

import contextlib
import io
import itertools
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication, QScrollArea

with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
    import main as game


def character(alive=True):
    return {"alive": alive, "profile": [], "memory": [], "key-takeaways": []}


class RuntimeRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["runtime-regressions"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.patches = contextlib.ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(
            socket.socket, "connect", side_effect=AssertionError("Network access in offline test")
        ))
        self.openai_constructor = self.patches.enter_context(patch.object(
            game, "OpenAI", side_effect=AssertionError("Unexpected API client creation")
        ))
        self.patches.enter_context(patch.object(game, "QMediaPlayer"))
        self.patches.enter_context(patch.object(game, "summarise_conversation", return_value="Summary"))
        self.patches.enter_context(patch.object(game, "generate_conversation_speech", return_value="A reply."))
        self.patches.enter_context(patch.object(game, "generate_voting_prompt", return_value="NONE"))
        self.window = game.GameWindow()
        self.app.processEvents()
        self.window.player_name = "Alex"
        self.window.characters = {
            "Jerry": character(), "Dave": character(), "Hank": character(),
            "Debra": character(False),
        }

    def tearDown(self):
        for worker in self.window.active_workers:
            self.assertFalse(worker.isRunning(), "Test left a running worker")
        self.window.close_escape_menu()
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def begin_vote(self):
        self.window.phase = "conversation"
        with patch.object(game.AIVoteWorker, "start"):
            self.window.run_voting_phase()
        self.assertEqual(self.window.expected_voters, {"Alex", "Jerry", "Dave", "Hank"})

    def test_title_screen_opens_without_api_key_or_client(self):
        self.assertEqual(self.window.phase, "start")
        self.assertTrue(self.window.start_button.isVisible())
        self.openai_constructor.assert_not_called()

    def test_blank_name_error_remains_visible_on_start_screen(self):
        self.window.player_name_input.setText("   ")
        self.window.start_button.click()
        self.assertEqual(self.window.phase, "start")
        self.assertTrue(self.window.player_name_label.isVisible())
        self.assertIn("Please enter your name", self.window.player_name_label.text())
        self.assertTrue(self.window.how_to_play_button.isVisible())

    def test_escape_menu_timer_runs_and_menu_closes(self):
        QTest.keyClick(self.window, Qt.Key_Escape)
        QTest.qWait(40)
        self.assertTrue(self.window._esc_overlay.isVisible())
        QTest.keyClick(self.window, Qt.Key_Escape)
        self.assertTrue(self.window._esc_overlay is None or self.window._esc_overlay.isHidden())

    def test_weapon_picker_scrolls_to_every_card_at_laptop_size(self):
        self.window.showNormal()
        self.window.setFixedSize(1366, 768)
        self.window.player_name_input.setText("Alex")
        self.window.start_button.click()
        self.app.processEvents()
        scroll = self.window.weapon_scroll
        self.assertIsInstance(scroll, QScrollArea)
        self.assertTrue(scroll.isVisible())
        self.assertLessEqual(scroll.width(), self.window.width())
        self.assertEqual(self.window.weapon_grid.count(), len(self.window.weapons))
        self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
        self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
        last_card = self.window.weapon_grid.itemAt(len(self.window.weapons) - 1).widget()
        scroll.ensureWidgetVisible(last_card)
        self.app.processEvents()
        self.assertTrue(scroll.viewport().rect().intersects(
            last_card.rect().translated(last_card.mapTo(scroll.viewport(), last_card.rect().topLeft()))
        ))

    def test_source_resources_load_outside_working_directory(self):
        original_cwd = os.getcwd()
        expected = game.load_system_prompt()
        with tempfile.TemporaryDirectory() as directory:
            try:
                os.chdir(directory)
                self.assertEqual(game.load_system_prompt(), expected)
                self.assertTrue(Path(game.resource_path("icons/audio.png")).is_file())
                self.assertTrue(Path(game.resource_path("House_images/Scaled/Attic.png")).is_file())
            finally:
                os.chdir(original_cwd)

    def test_voting_waits_for_separate_player_in_every_arrival_order(self):
        for order in itertools.permutations(("Alex", "Jerry", "Dave", "Hank")):
            with self.subTest(order=order), patch.object(self.window, "add_continue_button") as advance:
                self.begin_vote()
                for index, voter in enumerate(order):
                    if voter == "Alex":
                        self.window.player_vote("NONE")
                    else:
                        self.window.collect_ai_vote(voter, "NONE")
                    self.assertEqual(self.window.voting_resolved, index == len(order) - 1)
                    self.assertEqual(advance.call_count, int(index == len(order) - 1))
                self.window.tally_votes()
                self.window.collect_ai_vote("Jerry", "Alex")
                self.window.player_vote("Jerry")
                advance.assert_called_once()
                self.assertEqual(self.window.votes["Alex"], "NONE")
                self.assertFalse(self.window.awaiting_player_vote)

    def test_voting_buttons_stay_enabled_while_ai_votes_are_pending(self):
        self.window.phase = "conversation"
        with patch.object(game.AIVoteWorker, "start"):
            self.window.call_vote()
        self.assertTrue(self.window.isEnabled())
        self.assertTrue(self.window.vote_buttons_widget.isEnabled())

    def test_missing_ballots_produce_no_partial_results(self):
        self.begin_vote()
        self.window.collect_ai_vote("Jerry", "Dave")
        self.window.tally_votes()
        self.assertFalse(self.window.voting_resolved)
        self.assertNotIn("Jerry voted", self.window.output_area.toPlainText())

    def test_invalid_ai_ballots_skip_instead_of_targeting_dead_or_self(self):
        for vote in ("Debra", "Jerry", "Unknown", "", None):
            with self.subTest(vote=vote):
                self.assertEqual(game.validate_vote("Jerry", vote, self.window.characters, "Alex"), "NONE")
                worker = game.AIVoteWorker("Jerry", self.window.characters, "rules", "summary", "Alex")
                emitted = QSignalSpy(worker.vote_ready)
                with patch.object(game, "generate_voting_prompt", return_value=vote):
                    worker.run()
                self.assertEqual(list(emitted[0]), ["Jerry", "NONE"])
        self.assertEqual(game.validate_vote("Jerry", "Alex", self.window.characters, "Alex"), "Alex")
        self.assertEqual(game.validate_vote("Jerry", "Dave", self.window.characters, "Alex"), "Dave")

    def test_duplicate_or_dead_voters_cannot_overwrite_ballots(self):
        self.begin_vote()
        self.window.collect_ai_vote("Jerry", "Dave")
        self.window.collect_ai_vote("Jerry", "Alex")
        self.window.collect_ai_vote("Debra", "Alex")
        self.window.collect_ai_vote("Alex", "Jerry")
        self.assertEqual(self.window.votes, {"Jerry": "Dave"})

    def test_complete_vote_can_execute_an_npc_once(self):
        self.begin_vote()
        with patch.object(self.window, "add_continue_button") as advance:
            self.window.player_vote("Dave")
            self.window.collect_ai_vote("Jerry", "Dave")
            self.window.collect_ai_vote("Hank", "Dave")
            self.assertTrue(self.window.characters["Dave"]["alive"])
            self.window.collect_ai_vote("Dave", "Jerry")
            self.window.tally_votes()
            self.assertFalse(self.window.characters["Dave"]["alive"])
            advance.assert_called_once()

    def test_player_loss_sets_terminal_state_and_rejects_further_actions(self):
        self.begin_vote()
        self.window.player_vote("Jerry")
        for npc in ("Jerry", "Dave", "Hank"):
            self.window.collect_ai_vote(npc, "Alex")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.phase, "game_over")
        self.assertTrue(self.window.weapon_action_panel.isHidden())
        before = self.window.round_number
        self.window.run_action_phase()
        self.assertEqual(self.window.round_number, before)

    def test_room_gating_preserves_old_and_new_crime_scenes(self):
        self.window.old_crime_scenes = ["Attic"]
        self.window.new_crime_scenes = ["Kitchen"]
        self.window.set_room_buttons_enabled(False)
        self.window.set_room_buttons_enabled(True)
        self.assertFalse(self.window.room_buttons["Attic"].isEnabled())
        self.assertFalse(self.window.room_buttons["Kitchen"].isEnabled())
        self.assertTrue(self.window.room_buttons["Library"].isEnabled())
        self.assertFalse(self.window.room_labels["Library"].isHidden())

    def test_disabled_room_cannot_be_entered_by_direct_callback(self):
        self.window.phase = "action"
        self.window.room_buttons_widget.show()
        self.window.room_buttons["Attic"].setEnabled(False)
        self.window.rooms_visited = []
        self.window.new_crime_scenes = []
        self.window.choose_room("Attic")
        self.assertEqual(self.window.rooms_visited, [])
        self.assertEqual(self.window.new_crime_scenes, [])

    def test_c4_cooldown_does_not_close_sleeping_room(self):
        self.window.phase = "action"
        self.window.selected_weapon = {"id": "c4"}
        self.window.c4_cooldown_nights = 1
        self.window.rooms_visited = []
        self.window.new_crime_scenes = []
        self.window.room_assignments = {room: [] for room in self.window.rooms}
        self.window.room_buttons_widget.show()
        with patch.object(self.window, "update_witness_memories") as memories, patch.object(
            self.window, "run_conversation_phase"
        ) as conversation:
            self.window.choose_room("Attic")
        self.assertEqual(self.window.new_crime_scenes, [])
        self.assertEqual(self.window.rooms_visited, ["Attic"])
        memories.assert_called_once_with(None)
        conversation.assert_called_once()

    def test_pen_reveals_one_shared_signature_to_every_character(self):
        for roll, signature in ((0.01, "Alex"), (0.99, "Debra")):
            with self.subTest(roll=roll):
                self.window.selected_weapon = {"id": "pen"}
                self.window.pending_pen_note_text = "Trust nobody"
                self.window.victims_last_night = ["Debra"]
                self.window.new_crime_scenes = []
                for data in self.window.characters.values():
                    data["memory"] = []
                with patch.object(game.random, "random", return_value=roll) as random_roll, patch.object(
                    self.window, "run_conversation_loop"
                ), contextlib.redirect_stdout(io.StringIO()):
                    self.window.run_conversation_phase()
                random_roll.assert_called_once()
                notes = [next(line for line in data["memory"] if "note was found" in line)
                         for data in self.window.characters.values()]
                self.assertEqual(len(set(notes)), 1)
                self.assertIn("Trust nobody - Signed: " + signature, notes[0])

    def test_pen_without_victim_does_not_reveal_a_note(self):
        self.window.selected_weapon = {"id": "pen"}
        self.window.pending_pen_note_text = "Trust nobody"
        self.window.victims_last_night = []
        self.window.new_crime_scenes = []
        with patch.object(game.random, "random") as random_roll, patch.object(
            self.window, "run_conversation_loop"
        ), contextlib.redirect_stdout(io.StringIO()):
            self.window.run_conversation_phase()
        random_roll.assert_not_called()
        self.assertFalse(any("note was found" in line for data in self.window.characters.values()
                             for line in data["memory"]))

    def test_weapon_panel_stays_hidden_outside_room_selection(self):
        self.window.selected_weapon = {"id": "bare"}
        for phase in ("start", "weapon_select", "conversation", "voting", "game_over"):
            with self.subTest(phase=phase):
                self.window.phase = phase
                self.window.weapon_action_panel.show()
                self.window.position_weapon_panel()
                self.assertTrue(self.window.weapon_action_panel.isHidden())

    def test_stale_conversation_callbacks_cannot_resume_another_phase(self):
        self.window.current_conversation = ["Alex: Original"]
        self.window.most_recent_messageS = ["Alex: Original"]
        for phase, game_over in (("voting", False), ("action", False), ("conversation", True)):
            with self.subTest(phase=phase, game_over=game_over), patch.object(
                game.QThread, "start", side_effect=AssertionError("Stale callback launched a worker")
            ), patch.object(self.window, "run_conversation_loop") as resume:
                self.window.phase = phase
                self.window.game_over = game_over
                self.window.run_single_response("Jerry")
                self.window.run_multiple_responses()
                self.window.handle_single_response("Jerry", "Late reply")
                self.window.handle_multiple_responses({"Jerry": "Late reply"}, "Late summary")
                self.window.handle_best_response("Late reply", "Jerry")
                resume.assert_not_called()
                self.assertEqual(self.window.current_conversation, ["Alex: Original"])
                self.assertEqual(self.window.most_recent_messageS, ["Alex: Original"])

    def test_parallel_responses_emit_once_with_real_queued_signals(self):
        worker = game.MultipleResponseWorker(
            self.window.characters, "rules", ["Alex: Hello"], ["Alex: Hello"], ["Jerry", "Dave", "Hank"]
        )
        emitted = QSignalSpy(worker.all_responses_ready)
        worker.start()
        self.assertTrue(worker.wait(2000), "Response coordinator did not finish")
        children = list(worker.workers)
        try:
            for child in children:
                self.assertTrue(child.wait(2000), "Character worker did not finish")
            if not emitted:
                self.assertTrue(emitted.wait(2000), "Combined reply was never emitted")
            self.app.processEvents()
            worker.check_all_workers_done()
            worker.check_all_workers_done()
            self.assertEqual(len(emitted), 1)
            self.assertEqual(dict(emitted[0][0]), {
                "Jerry": "A reply.", "Dave": "A reply.", "Hank": "A reply."
            })
            self.assertEqual(emitted[0][1], "Summary")
        finally:
            worker.wait(2000)
            for child in children:
                child.wait(2000)


if __name__ == "__main__":
    unittest.main()
