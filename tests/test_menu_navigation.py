"""Offline Qt coverage of the real menu and its existing start flow."""

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

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, Qt
from PyQt5.QtGui import QFont, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QPushButton, QStyle, QStyleOptionButton

import main as game


class MenuNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["menu-navigation"])
        cls.app.setQuitOnLastWindowClosed(False)
        if sys.platform == "win32" and cls.app.platformName() == "offscreen":
            QFont.insertSubstitution("Helvetica", "Segoe UI")

    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        directory = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.dict(os.environ, {
            "ALIBAI_PROFILE_PATH": str(directory / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(directory / "preferences.ini"), "ALIBAI_DEV_MODE": "0",
        }))
        self.stack.enter_context(patch.object(game, "QMediaPlayer"))
        self.stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline test")))
        self.stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        self.window = game.GameWindow()
        self.window.showNormal()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def rect_in_window(self, widget):
        rectangle = widget.rect()
        rectangle.moveTopLeft(widget.mapTo(self.window, QPoint(0, 0)))
        return rectangle

    def assert_mute_icon_fits(self):
        button = self.window.mute_button
        option = QStyleOptionButton()
        button.initStyleOption(option)
        contents = button.style().subElementRect(QStyle.SE_PushButtonContents, option, button)
        requested = button.iconSize()
        self.assertEqual(requested.width(), requested.height())
        self.assertGreater(requested.width(), 0)
        self.assertLessEqual(requested.width(), contents.width())
        self.assertLessEqual(requested.height(), contents.height())
        self.assertFalse(button.icon().pixmap(requested).isNull())

    def test_repeated_main_menu_preserves_window_session_and_resets_each_run(self):
        window = self.window
        window.resize(1100, 760)
        self.app.processEvents()
        native_id, geometry, state = int(window.winId()), window.geometry(), window.windowState()
        services = (window.centralWidget(), window.dictation_bindings, window.dictation_recorder,
                    window.preferences, window.profile_store, window.media_player, window.effect_player)
        window.player_name_input.setText("Literal entered name")
        window.toggle_mode()
        window.toggle_mode()
        self.assertEqual(window.difficulty_button.text(), "Mode: Coward")
        window.toggle_mute()
        window.media_player.play.reset_mock()
        configuration = window.ai_runtime.config
        runs = set()
        for _ in range(3):
            window.start_button.click()
            self.assertEqual(window.phase, "weapon_select")
            runs.add(window.run_id)
            window.select_weapon_and_start(window.weapons[0])
            self.assertEqual(window.phase, "action")
            self.assertTrue(window.room_buttons_widget.isVisible())
            window.add_continue_button()
            window.display_play_again_button()
            old_trace, old_runtime = window.trace, window.ai_runtime
            window.open_escape_menu()
            button = window.overlay_host.current_page.findChild(QPushButton, "pauseRestart")
            button.click()
            self.app.processEvents()
            self.assertEqual(window.phase, "start")
            self.assertEqual(int(window.winId()), native_id)
            self.assertEqual(window.geometry(), geometry)
            self.assertEqual(window.windowState(), state)
            self.assertEqual((window.centralWidget(), window.dictation_bindings, window.dictation_recorder,
                              window.preferences, window.profile_store, window.media_player, window.effect_player), services)
            self.assertEqual(window.player_name_input.text(), "Literal entered name")
            self.assertEqual(window.difficulty_button.text(), "Mode: Coward")
            self.assertTrue(window.show_room_allocations)
            self.assertFalse(window.music_enabled)
            self.assertFalse(window.sfx_enabled)
            self.assertEqual(window.characters, {})
            self.assertEqual(window.history_records, [])
            self.assertEqual(window.public_dialogue, [])
            self.assertEqual(window.inventory.entries, ())
            self.assertIsNone(window.selected_weapon)
            self.assertIsNot(window.trace, old_trace)
            self.assertIsNot(window.ai_runtime, old_runtime)
            self.assertEqual(window.ai_runtime.config, configuration)
            self.assertTrue(window.menu_widget.isVisible())
            self.assertFalse(window.room_buttons_widget.isVisible())
            self.assertFalse(window.overlay_host.owner_cancelled)
            self.assertFalse(any(button.isVisible() for name in ("playAgain", "continueRun")
                                 for button in window.findChildren(QPushButton, name)))
        self.assertEqual(len(runs), 3)
        window.media_player.play.assert_not_called()
        window.effect_player.stop.assert_called()

    def test_previous_run_trace_notice_cannot_write_into_new_run(self):
        previous = self.window.trace
        self.window.restart_game()
        previous.on_error("Old trace failed")
        self.app.processEvents()
        self.assertEqual(self.window.output_area.toPlainText(), "")
        self.window.trace.on_error("Current trace failed")
        self.app.processEvents()
        self.assertIn("Current trace failed", self.window.output_area.toPlainText())

    def test_fullscreen_and_unsaved_progress_survive_navigation(self):
        from progression import ProfileError, ProgressEvent
        window = self.window
        window.showFullScreen()
        self.app.processEvents()
        native_id, state = int(window.winId()), window.windowState()
        event = ProgressEvent(window.run_id, 1, "survived_vote")
        window.pending_progress.append(event)
        profile = game.ProfileSnapshot(unlocked=frozenset({"knife"}))
        with patch.object(window.profile_store, "read", return_value=profile):
            window.restart_game()
        self.app.processEvents()
        self.assertEqual(int(window.winId()), native_id)
        self.assertEqual(window.windowState(), state)
        self.assertEqual(window.pending_progress, [event])
        self.assertEqual(window.profile.unlocked, frozenset({"knife"}))
        self.assertTrue(window.progress_retry_button.isVisible())
        self.assertNotEqual(window.run_id, event.run_id)
        with patch.object(window.profile_store, "record", side_effect=ProfileError("Save unavailable")):
            window.retry_progress()
        self.assertEqual(window.pending_progress, [event])
        self.assertTrue(window.progress_retry_button.isVisible())

    def test_repeated_resizes_keep_real_controls_in_bounds_and_clear_of_corners(self):
        window = self.window
        for width, height in ((800, 600), (1280, 800), (2560, 1440), (3440, 1440), (800, 600)):
            window.resize(width, height)
            self.app.processEvents()
            self.assertEqual((window.width(), window.height()), (width, height))
            self.assert_mute_icon_fits()
            window.play_tab_button.click()
            self.app.processEvents()
            self.assertEqual(window.title_label.text(), "AlibAi")
            self.assertEqual(QFontInfo(window.title_label.font()).family(), window.pixel_font_family)
            self.assertLessEqual(window.play_content.width(), 1000)
            content = self.rect_in_window(window.play_content)
            self.assertLessEqual(abs(content.center().x() - window.rect().center().x()), 1)
            for control in (window.title_label, window.player_name_label, window.player_name_input,
                            window.difficulty_button, window.start_button, window.play_tab_button,
                            window.items_tab_button, window.mute_button, window.how_to_play_button):
                with self.subTest(size=(width, height), control=control.text()):
                    self.assertTrue(control.isVisible())
                    rectangle = self.rect_in_window(control)
                    self.assertTrue(window.rect().contains(rectangle), rectangle)
                    if control not in (window.mute_button, window.how_to_play_button):
                        for corner in (window.mute_button, window.how_to_play_button):
                            self.assertFalse(rectangle.intersects(self.rect_in_window(corner)))
            self.assertLessEqual(window.title_label.fontMetrics().horizontalAdvance("AlibAi"),
                                 window.title_label.width())
            window.items_tab_button.click()
            self.app.processEvents()
            self.assertIs(window.menu_stack.currentWidget(), window.item_catalogue_page)
            self.assertTrue(window.item_catalogue.isVisible())
            for corner in (window.mute_button, window.how_to_play_button):
                self.assertFalse(self.rect_in_window(window.item_catalogue).intersects(self.rect_in_window(corner)))
            self.assertEqual(window.item_catalogue.scroll.horizontalScrollBar().maximum(), 0)
            name = window.item_catalogue.findChild(QLabel, "itemName_banana")
            window.item_catalogue.cards["banana"].setFocus()
            detail = window.item_catalogue.detail_description
            self.assertEqual(QFontInfo(name.font()).family(), window.pixel_font_family)
            self.assertNotEqual(QFontInfo(detail.font()).family(), window.pixel_font_family)
            self.assertEqual(QFontInfo(detail.font()).pixelSize(), 18)

    def test_keyboard_tabs_and_start_weapon_house_routing(self):
        window = self.window
        window.resize(800, 600)
        self.app.processEvents()
        window.play_tab_button.setFocus()
        QTest.keyClick(window.play_tab_button, Qt.Key_Right)
        self.assertIs(self.app.focusWidget(), window.items_tab_button)
        self.assertIs(window.menu_stack.currentWidget(), window.item_catalogue_page)
        self.assertTrue(window.item_catalogue.isVisible())
        QTest.keyClick(window.items_tab_button, Qt.Key_Tab)
        self.assertIsNot(self.app.focusWidget(), window.items_tab_button)
        window.play_tab_button.setFocus()
        QTest.keyClick(window.play_tab_button, Qt.Key_Space)
        self.assertIs(window.menu_stack.currentWidget(), window.play_page)
        window.player_name_input.setText("Alex")
        window.difficulty_button.click()
        self.assertEqual(window.difficulty_button.text(), "Mode: Super Hard")
        window.difficulty_button.click()
        self.assertEqual(window.difficulty_button.text(), "Mode: Coward")
        window.start_button.click()
        self.app.processEvents()
        self.assertEqual(window.phase, "weapon_select")
        self.assertTrue(window.weapon_scroll.isVisible())
        self.assertFalse(window.menu_widget.isVisible())
        bare = window.weapon_grid.itemAtPosition(0, 0).widget()
        self.assertTrue(bare.isEnabled())
        QTest.mouseClick(bare, Qt.LeftButton)
        self.app.processEvents()
        self.assertEqual(window.phase, "action")
        self.assertTrue(window.room_buttons_widget.isVisible())
        self.assertEqual(window.selected_weapon["id"], "bare")
        for size in ((1280, 800), (800, 600)):
            window.resize(*size)
            self.app.processEvents()
            self.assert_mute_icon_fits()

    def test_mode_keeps_rendered_colour_through_hover_focus_and_repeated_clicks(self):
        button = self.window.difficulty_button

        def assert_tint(green):
            self.app.processEvents()
            image = button.grab().toImage()
            coloured = 0
            for y in range(image.height()):
                for x in range(image.width()):
                    pixel = image.pixelColor(x, y)
                    dominant, secondary = ((pixel.green(), pixel.red()) if green
                                           else (pixel.red(), pixel.green()))
                    coloured += dominant > 100 and dominant > secondary * 1.2
            self.assertGreater(coloured, 20)

        assert_tint(True)
        QTest.mouseMove(button, button.rect().center())
        assert_tint(True)
        for green in (False, True, False, True):
            QTest.mouseClick(button, Qt.LeftButton)
            self.assertEqual(self.window.show_room_allocations, green)
            assert_tint(green)
            QTest.mouseMove(self.window, QPoint(0, self.window.height() - 1))
            assert_tint(green)


if __name__ == "__main__":
    unittest.main()
