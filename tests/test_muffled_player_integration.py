"""Offline real controls and callback guards for a human in a muffled body."""
import contextlib
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QApplication
import main as game
from muffled import TONES, muffled_sound


class MuffledPlayerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["muffled-player-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(Path(directory) / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(Path(directory) / "settings.ini"), "ALIBAI_DEV_MODE": "0",
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
        entry = self.window.inventory.grant("muffle")
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, self.window.body_id("Dave")))
        self.window.exchange_player_mind(self.window.body_id("Dave"))

    def tearDown(self):
        for worker in list(self.window.active_workers):
            self.assertTrue(worker.wait(3000))
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def address_player(self):
        self.window.append_speech("Detective", "Dave, your account?", "Dave")
        self.window.restore_conversation_decision()

    def test_all_five_tones_replace_raw_text_and_drop_addressee(self):
        for tone in TONES:
            with self.subTest(tone=tone):
                self.address_player()
                self.window.muffled_player_tone.setCurrentIndex(tone + 2)
                self.window.interject_input.setText("Raw private text must never be spoken.")
                index = self.window.recipient_selector.findData(self.window.body_id("Jerry"))
                self.window.recipient_selector.setCurrentIndex(index)
                self.assertFalse(self.window.interject_input.isEnabled())
                self.assertTrue(self.window.recipient_controls.isHidden())
                self.assertFalse(self.window.can_dictate())
                self.assertTrue(self.window.muffled_player_controls.isVisibleTo(self.window))
                self.assertTrue(self.window.say_button.isEnabled())
                self.window.say_button.click()
                line = self.window.public_dialogue[-1]
                self.assertEqual((line.speaker, line.text, line.addressee),
                                 ("Dave", muffled_sound("Dave", tone), None))
                self.assertFalse(self.window.player_reply_required())
                self.assertTrue(self.window.muffled_player_controls.isHidden())
        self.assertFalse(any("Raw private" in line.text for line in self.window.public_dialogue))

    def test_open_floor_blocks_direct_callbacks_and_allows_normal_pass(self):
        before = tuple(self.window.public_dialogue)
        self.window.interject_conversation()
        self.window.choose_speaker_recipient(self.window.body_id("Jerry"))
        self.window.interject_input.setText("Injected draft")
        self.window.send_interjection()
        self.assertFalse(self.window.append_speech("Dave", "Injected direct append", "Jerry"))
        self.window.toggle_dictation()
        self.assertFalse(self.window.dictation_recorder.is_recording)
        self.assertEqual(tuple(self.window.public_dialogue), before)
        self.window.restore_conversation_decision()
        self.assertFalse(self.window.interject_button.isEnabled())
        self.assertTrue(self.window.pass_button.isEnabled())
        with patch.object(self.window, "request_next_group_turn") as advance:
            self.window.pass_conversation()
            advance.assert_called_once()
        self.assertTrue(self.window.can_use_items())

    def test_required_reply_guards_pass_and_append_even_if_buttons_are_forced(self):
        self.address_player()
        with patch.object(self.window, "request_next_group_turn") as advance:
            self.window.pass_conversation()
            advance.assert_not_called()
        self.window.ai_busy = True
        self.assertFalse(self.window.append_speech("Dave", "Injected while busy"))
        self.window.send_interjection()
        self.assertEqual(self.window.public_dialogue[-1].speaker, "Detective")
        self.window.ai_busy = False
        self.window.muffled_player_tone.setCurrentIndex(0)
        self.assertTrue(self.window.append_speech("Dave", "Injected", "Jerry"))
        self.assertEqual(self.window.public_dialogue[-1].text, muffled_sound("Dave", -2))
        self.assertIsNone(self.window.public_dialogue[-1].addressee)

    def test_expiry_and_body_change_restore_ordinary_input(self):
        self.address_player()
        events = self.window.item_rules.begin_night(2, self.window.body_states())
        self.window.apply_item_events(events)
        self.window.restore_conversation_decision()
        self.assertFalse(self.window.player_is_muffled())
        self.assertTrue(self.window.interject_input.isEnabled())
        self.assertFalse(self.window.interject_input.isHidden())
        self.assertTrue(self.window.can_dictate())
        self.assertTrue(self.window.muffled_player_controls.isHidden())

    def test_changing_body_invalidates_pending_dictation_and_stale_audio(self):
        self.window.exchange_player_mind(self.window.body_id("Jerry"))
        self.window.interject_conversation()
        self.assertTrue(self.window.can_dictate())
        context = (self.window.dictation_session, self.window.round_number)
        self.window.dictation_context = context
        generation = self.window.dictation_recorder.generation
        self.window.exchange_player_mind(self.window.body_id("Dave"))
        self.assertFalse(self.window.dictation_is_current(generation, context))
        with patch.object(self.window, "start_worker") as start:
            self.window.transcribe_recording(generation, b"obsolete audio")
            start.assert_not_called()
        self.assertTrue(self.window.player_is_muffled())
        self.assertEqual(self.window.interject_input.text(), "")
