"""Offline checks for persisted unlock delivery and shared overlay choice ownership."""

import contextlib
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

from PyQt5.QtCore import QCoreApplication, QEvent, Qt
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

import ai_runtime
import main as game
from items import ITEM_CATALOGUE, DraftState
from progression import ProfileError, ProgressEvent
from unlock_ui import UnlockPage, unlock_presentation


class UnlockProgressTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["unlock-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.app.setStyle("Fusion")
        if sys.platform == "win32":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(Path(directory) / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(Path(directory) / "settings.ini"),
            "ALIBAI_DEV_MODE": "0",
        }))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.stack.enter_context(patch.object(ai_runtime, "OpenAI", side_effect=AssertionError("No API client")))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.stack.enter_context(patch.object(game.GameWindow, "run_conversation_loop"))
        self.window = game.GameWindow()
        self.window.showNormal()
        self.window.resize(800, 600)
        self.window.phase = "conversation"
        self.window.player_name = "Alex"
        self.window.characters = {"Dave": {"alive": True, "profile": [], "experiences": [], "dispositions": []}}
        self.events()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.events()

    def events(self):
        self.app.processEvents()
        QTest.qWait(1)
        self.app.processEvents()

    def event(self, number, kind):
        return ProgressEvent("test-run", number, kind)

    def save(self, *events):
        self.window.pending_progress.extend(events)
        result = self.window.flush_progress()
        self.events()
        return result

    def test_actual_store_batches_weapon_and_item_unlocks_once_and_retry_is_silent(self):
        event = self.event(1, "survived_vote")
        self.assertTrue(self.save(event))
        self.assertIn("pen", self.window.profile.unlocked)
        self.assertIn("guardian_angel", self.window.profile.unlocked)
        host = self.window.overlay_host
        pages = []
        while host.current_page is not None:
            self.assertIsInstance(host.current_page, UnlockPage)
            self.assertEqual(host.current_title, "New Unlock!")
            self.assertIs(self.app.activeModalWidget(), host)
            self.assertEqual(len([w for w in self.app.topLevelWidgets()
                                  if isinstance(w, QDialog) and w.isVisible()]), 1)
            pages.append(host.current_page.presentation.content_id)
            QTest.keyClick(host.current_page.continue_button, Qt.Key_Return)
            self.events()
        self.assertCountEqual(pages, ("pen", "guardian_angel"))
        self.assertTrue(self.save(event))
        self.assertEqual(host.pending_unlock_count, 0)
        self.assertIsNone(host.current_page)
        self.assertTrue(self.window.progress_notice.isHidden())

    def test_partial_save_keeps_successful_unlock_and_unsaved_event_retryable(self):
        first, second = self.event(1, "player_kill"), self.event(2, "survived_vote")
        original = self.window.profile_store.record

        def record(event):
            if event == second:
                raise ProfileError("Disk unavailable")
            return original(event)

        with patch.object(self.window.profile_store, "record", side_effect=record):
            self.assertFalse(self.save(first, second))
        self.assertEqual(self.window.pending_progress, [second])
        self.assertEqual(self.window.overlay_host.current_page.presentation.content_id, "knife")
        self.assertFalse(self.window.progress_retry_button.isHidden())
        self.assertIn("Disk unavailable", self.window.progress_notice.text())
        self.assertTrue(self.save())
        self.assertIn("guardian_angel", self.window.profile.unlocked)
        self.assertEqual(self.window.pending_progress, [])
        self.assertEqual(self.window.overlay_host.pending_unlock_count, 2)
        self.assertNotIn("Unlocked for your next run", self.window.progress_notice.text())

    def test_factory_failure_preserves_saved_unlock_without_unsaved_retry_message(self):
        with patch.object(self.window, "build_unlock_page", side_effect=RuntimeError("private error")):
            self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIn("knife", self.window.profile_store.read().unlocked)
        self.assertEqual(self.window.pending_progress, [])
        self.assertIn("Your unlock is saved", self.window.progress_notice.text())
        self.assertNotIn("private error", self.window.progress_notice.text())
        self.assertTrue(self.window.progress_retry_button.isHidden())
        self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIsNone(self.window.overlay_host.current_page)

    def test_factory_failure_does_not_overwrite_a_real_save_error(self):
        original = self.window.profile_store.record
        def record(event):
            if event.sequence == 2:
                raise ProfileError("Disk unavailable")
            return original(event)
        with patch.object(self.window.profile_store, "record", side_effect=record), patch.object(
                self.window, "build_unlock_page", side_effect=RuntimeError("private error")):
            self.assertFalse(self.save(self.event(1, "player_kill"), self.event(2, "survived_vote")))
        self.assertIn("Disk unavailable", self.window.progress_notice.text())
        self.assertIn("Your unlock is saved", self.window.progress_notice.text())
        self.assertFalse(self.window.progress_retry_button.isHidden())

    def test_real_inline_choice_blocks_unlock_until_explicit_leave_continues(self):
        self.window.phase = "action"
        self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))
        continued = []
        self.window.offer_room_item("Kitchen", lambda: continued.append(True))
        draft = self.window.draft_page
        self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertTrue(draft.isVisible())
        self.assertEqual(continued, [])
        draft.leave_button.click()
        self.events()
        self.assertEqual(continued, [True])
        self.assertIsNone(self.window.draft_page)
        self.assertIsInstance(self.window.overlay_host.current_page, UnlockPage)

    def test_weapon_choice_blocks_until_actual_weapon_selection_finishes(self):
        self.window.phase = "weapon_select"
        self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIsNone(self.window.overlay_host.current_page)
        with patch.object(self.window, "run_action_phase", side_effect=lambda: setattr(self.window, "phase", "action")):
            self.window.select_weapon_and_start(self.window.weapons[0])
        self.events()
        self.assertIsInstance(self.window.overlay_host.current_page, UnlockPage)

    def test_real_legacy_weapon_editor_blocks_until_close(self):
        self.window.phase = "action"
        self.window.selected_weapon = next(w for w in self.window.weapons if w["id"] == "pen")
        self.window.open_pen_editor()
        editor = self.window._weapon_windows["pen"]
        self.assertTrue(editor.isVisible())
        self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIsNone(self.window.overlay_host.current_page)
        editor.close()
        self.events()
        self.assertIsInstance(self.window.overlay_host.current_page, UnlockPage)

    def test_active_inventory_survives_queued_unlock(self):
        self.window.inventory.grant("banana")
        self.window.open_inventory()
        inventory = self.window.inventory_page
        self.assertIs(self.window.overlay_host.current_page, inventory)
        self.assertTrue(self.save(self.event(1, "player_kill")))
        self.assertIs(self.window.overlay_host.current_page, inventory)
        self.assertEqual(self.window.overlay_host.pending_unlock_count, 1)
        self.window.overlay_host.close_page()
        self.events()
        self.assertIsInstance(self.window.overlay_host.current_page, UnlockPage)

    def test_end_game_unlock_is_allowed_but_cancelled_owner_cannot_show_stale_pages(self):
        self.window.game_over = True
        self.window.phase = "game_over"
        self.assertTrue(self.save(self.event(1, "three_kills_one_run")))
        self.assertIsInstance(self.window.overlay_host.current_page, UnlockPage)
        self.window.overlay_host.cancel_owner()
        self.events()
        self.assertIsNone(self.window.overlay_host.current_page)
        self.assertEqual(self.window.overlay_host.pending_unlock_count, 0)
        self.assertIn("c4", self.window.profile_store.read().unlocked)

    def test_all_metadata_uses_canonical_descriptions_and_real_sprites_at_800_by_600(self):
        host = self.window.overlay_host
        for content in game.CONTENT_CATALOGUE:
            if content.default:
                continue
            with self.subTest(content=content.content_id):
                page = self.window.build_unlock_page(content.content_id)
                self.assertTrue(host.open_page(page, title="New Unlock!"))
                self.events()
                data = page.presentation
                if data.content_id in ITEM_CATALOGUE:
                    self.assertEqual(data.description, ITEM_CATALOGUE[data.content_id].description)
                else:
                    self.assertEqual(data.description, next(w["desc"] for w in self.window.weapons if w["id"] == data.content_id))
                self.assertFalse(page.sprite.pixmap.isNull())
                self.assertNotEqual(QFontInfo(page.effect_label.font()).family(), self.window.pixel_font_family)
                self.assertEqual(page.effect_label.font().pixelSize(), 18)
                self.assertEqual(QFontInfo(page.continue_button.font()).family(), self.window.pixel_font_family)
                self.assertEqual(page.continue_button.font().pixelSize(), self.window.speaker_font.pixelSize())
                self.assertLessEqual(host.width(), 800)
                self.assertLessEqual(host.height(), 600)
                self.assertTrue(page.rect().contains(page.continue_button.geometry()))
                self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
                self.assertIs(self.app.focusWidget(), page.continue_button)
                QTest.keyClick(page.continue_button, Qt.Key_Space)
                self.events()
                self.assertIsNone(host.current_page)

    def test_packaged_resource_root(self):
        with tempfile.TemporaryDirectory() as directory:
            sprite = Path(directory) / "icons" / "items" / "guardian_angel.png"
            sprite.parent.mkdir(parents=True)
            sprite.write_bytes((Path(game.__file__).parent / "icons/items/guardian_angel.png").read_bytes())
            with patch.object(sys, "_MEIPASS", directory, create=True):
                page = self.window.build_unlock_page("guardian_angel")
                self.assertFalse(page.sprite.pixmap.isNull())
                page.deleteLater()
        metadata = unlock_presentation("guardian_angel", self.window.weapons, ITEM_CATALOGUE)
        self.assertEqual(metadata.name, ITEM_CATALOGUE["guardian_angel"].name)


if __name__ == "__main__":
    unittest.main()
