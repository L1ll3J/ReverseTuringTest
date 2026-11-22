"""Offline regression checks using the real Qt widgets and worker signals.

Run with: python -m unittest discover -s tests -v
"""

import contextlib
import itertools
import os
from pathlib import Path
import socket
import sys
import tempfile
from threading import Barrier, Event
from time import monotonic
import unittest
from unittest.mock import Mock, patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QScrollArea

with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
    import main as game
    import ai_runtime


def character(alive=True):
    return {"alive": alive, "profile": [], "experiences": [], "dispositions": []}


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
            ai_runtime, "OpenAI", side_effect=AssertionError("Unexpected API client creation")
        ))
        self.patches.enter_context(patch.object(game, "QMediaPlayer"))
        self.speech = self.patches.enter_context(patch.object(
            game.AIRuntime, "speech", return_value=game.SpeechProposal(None, "A reply.")
        ))
        self.vote = self.patches.enter_context(patch.object(game.AIRuntime, "vote", return_value="NONE"))
        self.selector = self.patches.enter_context(patch.object(
            game.AIRuntime, "select_reply", side_effect=lambda state, events, dialogue, proposals: next(iter(proposals))
        ))
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
        with patch.object(self.window, "start_ai_request"):
            self.window.run_voting_phase()
        self.assertEqual(self.window.expected_voters, {"Alex", "Jerry", "Dave", "Hank"})

    def wait_for(self, condition, message="The queued operation did not complete"):
        deadline = monotonic() + 3
        while not condition() and monotonic() < deadline:
            QTest.qWait(5)
        self.assertTrue(condition(), message)

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
        with patch.object(game.AIWorker, "start"):
            self.window.call_vote()
        self.assertTrue(self.window.isEnabled())
        self.assertTrue(self.window.vote_buttons_widget.isEnabled())
        for worker in list(self.window.active_workers):
            self.window.release_ai_worker(worker)

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
                self.begin_vote()
                self.window.collect_ai_vote("Jerry", vote)
                self.assertEqual(self.window.votes["Jerry"], "NONE")
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
                self.window.public_events = []
                with patch.object(game.random, "random", return_value=roll) as random_roll, patch.object(
                    self.window, "run_conversation_loop"
                ):
                    self.window.run_conversation_phase()
                random_roll.assert_called_once()
                notes = [next(event.text for event in self.window.build_character_context(name).public_events
                              if "note was found" in event.text) for name in self.window.characters]
                self.assertEqual(len(set(notes)), 1)
                self.assertIn("Trust nobody - Signed: " + signature, notes[0])
                self.assertEqual(sum("note was found" in event.text for event in self.window.public_events), 1)

    def test_pen_without_victim_does_not_reveal_a_note(self):
        self.window.selected_weapon = {"id": "pen"}
        self.window.pending_pen_note_text = "Trust nobody"
        self.window.victims_last_night = []
        self.window.new_crime_scenes = []
        with patch.object(game.random, "random") as random_roll, patch.object(
            self.window, "run_conversation_loop"
        ):
            self.window.run_conversation_phase()
        random_roll.assert_not_called()
        self.assertFalse(any("note was found" in event.text for event in self.window.public_events))

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
                proposal = game.SpeechProposal(None, "Late reply")
                snapshot = (self.window.public_state(), (), ())
                self.window.handle_single_response("Jerry", proposal)
                self.window.handle_multiple_responses({"Jerry": proposal}, snapshot)
                self.window.handle_best_response(proposal, "Jerry")
                resume.assert_not_called()
                self.assertEqual(self.window.current_conversation, ["Alex: Original"])
                self.assertEqual(self.window.most_recent_messageS, ["Alex: Original"])

    def test_restart_rejects_queued_old_results_and_creates_one_new_window(self):
        self.window.phase = "conversation"
        self.window.current_conversation = ["Alex: Original"]
        self.window.most_recent_messageS = ["Alex: Original"]
        self.window.NPCs = ["Jerry", "Dave", "Hank"]
        previous_quit_setting = self.app.quitOnLastWindowClosed()
        had_main_window = hasattr(self.app, "_main_window")
        previous_main_window = getattr(self.app, "_main_window", None)
        original_init = game.GameWindow.__init__
        created = []
        delivered = []

        def record_init(window):
            created.append(window)
            original_init(window)

        def deliver(callback, *args):
            delivered.append(callback.__name__)
            callback(*args)

        proposal = game.SpeechProposal(None, "Late reply")
        snapshot = (self.window.public_state(), (), ())
        callbacks = (
            (self.window.handle_single_response, ("Jerry", proposal)),
            (self.window.handle_multiple_responses, ({"Jerry": proposal}, snapshot)),
            (self.window.handle_best_response, (proposal, "Jerry")),
        )
        entered, release = Event(), Event()
        success = Mock()

        def operation():
            entered.set()
            if not release.wait(2):
                raise AssertionError("Old request was not released")
            return "late result"

        try:
            self.window.start_ai_request(operation, success, "Waiting for old request")
            self.wait_for(entered.is_set)
            with patch.object(game.GameWindow, "__init__", new=record_init), patch.object(
                game.QThread, "start"
            ) as start_worker, patch.object(self.window, "run_conversation_loop") as resume:
                for delay, (callback, args) in enumerate(callbacks, start=5):
                    QTimer.singleShot(delay, lambda callback=callback, args=args: deliver(callback, *args))
                self.window.restart_game()
                self.window.restart_game()
                QTimer.singleShot(10, release.set)
                QTest.qWait(80)
                self.wait_for(lambda: not self.window.active_workers)
                self.assertEqual(len(created), 1)
                self.assertIs(self.app._main_window, created[0])
                self.assertTrue(created[0].isVisible())
                self.assertEqual(created[0].phase, "start")
                self.assertEqual(len(delivered), 3)
                self.assertEqual(self.window.phase, "restarting")
                self.assertTrue(self.window.game_over)
                self.assertEqual(self.window.current_conversation, ["Alex: Original"])
                self.assertEqual(self.window.most_recent_messageS, ["Alex: Original"])
                start_worker.assert_not_called()
                resume.assert_not_called()
                success.assert_not_called()
        finally:
            release.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()
            self.app.setQuitOnLastWindowClosed(False)
            for window in created:
                window.close()
                window.deleteLater()
            self.app.processEvents()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            if had_main_window:
                self.app._main_window = previous_main_window
            elif hasattr(self.app, "_main_window"):
                del self.app._main_window
            self.app.setQuitOnLastWindowClosed(previous_quit_setting)

    def test_parallel_private_drafts_commit_only_the_selected_reply(self):
        self.window.phase = "conversation"
        self.window.append_speech("Alex", "Where were you?")
        self.window.add_public_event("The cellar is sealed.")
        for name in ("Jerry", "Dave", "Hank"):
            self.window.remember(name, "experience", name + " private observation")
            self.window.remember(name, "disposition", name + " private feeling")
        barrier = Barrier(3)
        contexts = {}
        selector_entered, release_selector = Event(), Event()

        def speech(context):
            contexts[context.character] = context
            barrier.wait(timeout=2)
            return game.SpeechProposal(None, context.character + " draft")

        def select(state, events, dialogue, proposals):
            selector_entered.set()
            if not release_selector.wait(2):
                raise AssertionError("Selector was not released")
            return "Dave"

        self.speech.side_effect = speech
        self.selector.side_effect = select
        before = tuple(self.window.public_dialogue)
        try:
            with patch.object(self.window, "run_conversation_loop"):
                self.window.run_multiple_responses()
                self.wait_for(selector_entered.is_set)
                self.assertEqual(tuple(self.window.public_dialogue), before)
                self.assertEqual(set(contexts), {"Jerry", "Dave", "Hank"})
                for name, context in contexts.items():
                    self.assertEqual([record.text for record in context.experiences], [name + " private observation"])
                    self.assertEqual([record.text for record in context.dispositions], [name + " private feeling"])
                    self.assertEqual(context.dialogue, before)
                    self.assertEqual(context.public_events, tuple(self.window.public_events))
                state, events, dialogue, proposals = self.selector.call_args.args
                self.assertEqual(dialogue, before)
                self.assertEqual(events, tuple(self.window.public_events))
                self.assertEqual(set(proposals), set(contexts))
                release_selector.set()
                self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
                self.assertEqual(self.window.public_dialogue[-1].speaker, "Dave")
                self.assertEqual(self.window.public_dialogue[-1].text, "Dave draft")
                self.assertEqual(len(self.window.public_dialogue), len(before) + 1)
                self.assertFalse(any("Jerry draft" in line or "Hank draft" in line
                                     for line in self.window.current_conversation))
                self.selector.assert_called_once()
        finally:
            release_selector.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()

    def test_speech_and_vote_snapshots_have_identical_private_and_public_evidence(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.selected_weapon = {"id": "bare"}
        self.window.rooms_visited = ["Attic"]
        self.window.had_to_change_rooms = False
        self.window.room_assignments = {
            "Attic": ["Debra", "Jerry"], "Kitchen": ["Dave"], "Library": ["Hank"]
        }
        self.window.update_witness_memories("Debra")
        self.window.remember("Jerry", "disposition", "I distrust Alex.")
        self.window.append_speech("Alex", "I was alone, Jerry is lying.")
        operations = []
        with patch.object(self.window, "start_ai_request", side_effect=lambda operation, *args: operations.append(operation)):
            self.window.run_single_response("Jerry")
            self.window.run_voting_phase()
        self.assertEqual(len(operations), 2)
        operations[0]()
        operations[1]()
        speech_context = self.speech.call_args.args[0]
        vote_contexts = {call.args[0].character: call.args[0] for call in self.vote.call_args_list}
        self.assertEqual(speech_context, vote_contexts["Jerry"])
        self.assertIn("You witnessed Alex kill Debra", speech_context.experiences[0].text)
        self.assertEqual(speech_context.dispositions[0].text, "I distrust Alex.")
        self.assertEqual(speech_context.dialogue[-1].text, "I was alone, Jerry is lying.")
        self.assertTrue(all(record.kind == "experience" for record in speech_context.experiences))
        self.assertEqual(vote_contexts["Dave"].experiences[0].text, "You (Dave) spent the night alone in the Kitchen.")
        self.assertFalse(any("witnessed" in record.text for record in vote_contexts["Dave"].experiences))

    def test_previous_round_dialogue_and_private_observations_survive_new_round(self):
        self.window.phase = "conversation"
        self.window.round_number = 1
        self.window.remember("Jerry", "experience", "I saw the first murder.")
        self.window.append_speech("Alex", "A claim from yesterday.")
        self.window.add_public_event("Yesterday's public finding.")
        self.window.round_number = 2
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        context = self.window.build_character_context("Jerry")
        self.assertEqual(context.experiences[0].text, "I saw the first murder.")
        self.assertEqual(context.dialogue[0], game.DialogueLine(1, "Alex", "A claim from yesterday."))
        self.assertEqual(context.public_events[0].text, "Yesterday's public finding.")
        self.assertEqual(context.dialogue[-1].round_number, 2)
        self.assertFalse(any("A claim from yesterday." in line for line in self.window.current_conversation))

    def test_vote_results_remain_public_facts_in_following_round(self):
        self.window.round_number = 1
        self.window.remember("Jerry", "experience", "A private observation")
        original_private = tuple(self.window.characters["Jerry"]["experiences"])
        self.begin_vote()
        self.window.player_vote("NONE")
        for name in ("Jerry", "Dave", "Hank"):
            self.window.collect_ai_vote(name, "NONE")
        results = tuple(self.window.public_events)
        self.assertEqual(sum("voted to skip" in event.text for event in results), 4)
        self.assertTrue(any("no one was voted out" in event.text for event in results))
        self.assertTrue(all(event.kind == "public_event" and event.source == "Voting" for event in results))
        self.window.round_number = 2
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_conversation_phase()
        for name in ("Jerry", "Dave", "Hank"):
            context = self.window.build_character_context(name)
            self.assertEqual(context.public_events[:len(results)], results)
        self.assertEqual(tuple(self.window.characters["Jerry"]["experiences"]), original_private)

    def test_failed_speech_retry_uses_frozen_input_without_inventing_a_reply(self):
        self.window.phase = "conversation"
        self.window.append_speech("Detective", "A question.")
        self.window.remember("Jerry", "experience", "Original observation")
        seen = []

        def speech(context):
            seen.append(context)
            if len(seen) == 1:
                raise game.AIRequestError("Temporary failure. Please retry.")
            return game.SpeechProposal(None, "Recovered reply")

        self.speech.side_effect = speech
        before = tuple(self.window.public_dialogue)
        with patch.object(self.window, "run_conversation_loop"):
            self.window.run_single_response("Jerry")
            self.wait_for(lambda: self.window.retry_action is not None and not self.window.active_workers)
            self.assertFalse(self.window.ai_busy)
            self.assertFalse(self.window.retry_button.isHidden())
            self.assertIn("Temporary failure", self.window.secondary_output_area.text())
            self.assertEqual(tuple(self.window.public_dialogue), before)
            self.window.remember("Jerry", "experience", "A later observation")
            self.window.retry_button.click()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
        self.assertIs(seen[0], seen[1])
        self.assertEqual([record.text for record in seen[1].experiences], ["Original observation"])
        self.assertEqual(self.window.public_dialogue[-1].text, "Recovered reply")
        self.assertTrue(self.window.retry_button.isHidden())
        self.assertIsNone(self.window.retry_action)

    def test_failed_vote_batch_keeps_player_ballot_and_retries_atomically(self):
        self.window.phase = "conversation"
        failed_once = Event()

        def vote(context):
            if context.character == "Dave" and not failed_once.is_set():
                failed_once.set()
                raise game.AIRequestError("Voting service unavailable. Please retry.")
            return "NONE"

        self.vote.side_effect = vote
        self.window.call_vote()
        self.window.player_vote("NONE")
        self.wait_for(lambda: self.window.retry_action is not None and not self.window.active_workers)
        self.assertEqual(self.window.votes, {"Alex": "NONE"})
        self.assertFalse(self.window.voting_resolved)
        self.window.retry_button.click()
        self.wait_for(lambda: self.window.voting_resolved and not self.window.active_workers)
        self.assertEqual(self.window.votes, {"Alex": "NONE", "Jerry": "NONE", "Dave": "NONE", "Hank": "NONE"})
        self.assertTrue(all(record.kind == "public_event" for record in self.window.public_events))
        self.assertTrue(all(not data["experiences"] for data in self.window.characters.values()))

    def test_same_phase_stale_result_does_not_clear_new_request_or_mutate_history(self):
        self.window.phase = "conversation"
        self.window.new_crime_scenes = []
        self.window.victims_last_night = []
        old_entered, release_old, new_entered, release_new = Event(), Event(), Event(), Event()
        old_success, new_success = Mock(), Mock()

        def operation(entered, release, result):
            entered.set()
            if not release.wait(2):
                raise AssertionError("Request was not released")
            return result

        try:
            self.window.start_ai_request(lambda: operation(old_entered, release_old, "old"), old_success, "Old request")
            old_worker = self.window.active_workers[0]
            self.wait_for(old_entered.is_set)
            with patch.object(self.window, "run_conversation_loop"):
                self.window.run_conversation_phase()
            self.window.start_ai_request(lambda: operation(new_entered, release_new, "new"), new_success, "New request")
            self.wait_for(new_entered.is_set)
            before = tuple(self.window.public_dialogue)
            release_old.set()
            self.wait_for(lambda: old_worker not in self.window.active_workers)
            self.assertTrue(self.window.ai_busy)
            self.assertEqual(tuple(self.window.public_dialogue), before)
            old_success.assert_not_called()
            new_success.assert_not_called()
            release_new.set()
            self.wait_for(lambda: not self.window.ai_busy and not self.window.active_workers)
            new_success.assert_called_once_with("new")
        finally:
            release_old.set()
            release_new.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()

    def test_close_retains_running_worker_until_safe_release_and_ignores_its_result(self):
        self.window.phase = "conversation"
        entered, release = Event(), Event()
        success = Mock()
        previous_quit = self.app.quitOnLastWindowClosed()
        previous_idle_flag = getattr(self.app, "_quit_when_idle", False)

        def operation():
            entered.set()
            if not release.wait(2):
                raise AssertionError("Request was not released")
            return "late result"

        try:
            with patch.object(self.app, "quit") as quit_app:
                self.window.start_ai_request(operation, success, "Waiting")
                self.wait_for(entered.is_set)
                worker = self.window.active_workers[0]
                self.app.setQuitOnLastWindowClosed(True)
                self.window.close()
                self.assertEqual(self.window.phase, "closed")
                self.assertIn(worker, self.app._ai_workers)
                quit_app.assert_not_called()
                release.set()
                self.wait_for(lambda: not self.window.active_workers)
                success.assert_not_called()
                self.assertNotIn(worker, self.app._ai_workers)
                quit_app.assert_called_once()
        finally:
            release.set()
            for worker in list(self.window.active_workers):
                worker.wait(3000)
            self.app.processEvents()
            self.app._quit_when_idle = previous_idle_flag
            self.app.setQuitOnLastWindowClosed(previous_quit)


if __name__ == "__main__":
    unittest.main()
