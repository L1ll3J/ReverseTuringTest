"""Offline production cutaway composition and public knowledge contracts."""
import hashlib
from pathlib import Path
import unittest
from unittest.mock import patch

from test_runtime_regressions import RuntimeRegressionTests
from PyQt5.QtCore import QPoint, QPointF, Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel
from PyQt5.QtGui import QFontInfo


class HouseIntegrationTests(unittest.TestCase):
    setUpClass = RuntimeRegressionTests.__dict__['setUpClass']
    setUp = RuntimeRegressionTests.setUp
    tearDown = RuntimeRegressionTests.tearDown

    def start_night(self, normal=True):
        self.window.show_room_allocations = normal
        self.window.player_name_input.setText("Alex")
        self.window.start_game()
        self.window.selected_weapon = next(w for w in self.window.weapons if w['id'] == 'bare')
        self.window.message_log.show()
        with patch('main.random.choice', side_effect=lambda choices: choices[0]):
            self.window.select_weapon_and_start(self.window.selected_weapon)
        self.window.showNormal()
        self.window.activateWindow()
        self.app.processEvents()

    def test_actual_house_and_sidebar_are_disjoint_at_all_target_sizes(self):
        self.start_night()
        self.window.transcript_button.show()
        for width, height in ((800, 600), (1280, 800), (2560, 1440), (3440, 1440)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window.position_corner_controls()
                QTest.qWait(10)
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                view = self.window.house_view
                sidebar = self.window.house_sidebar
                art = view.image_rect.toAlignedRect().translated(view.mapTo(self.window, QPoint()))
                side = sidebar.rect().translated(sidebar.mapTo(self.window, QPoint()))
                control = self.window.transcript_button.geometry()
                self.assertFalse(art.intersects(side))
                self.assertGreaterEqual(side.left() - art.right(), 16)
                self.assertLessEqual(side.left() - art.right(), 28)
                row = self.window.house_row
                row_center = row.mapTo(self.window, row.rect().center()).x()
                self.assertLessEqual(abs(row_center - self.window.rect().center().x()), 1)
                self.assertFalse(control.intersects(side))
                transcript = self.window.transcript_button
                self.assertLessEqual(transcript.height(), 56)
                self.assertLessEqual(transcript.width(), 220)
                self.assertTrue(transcript.isVisible())
                self.assertEqual(transcript.font().pixelSize(), 12)
                self.assertGreaterEqual(transcript.width(), transcript.fontMetrics().horizontalAdvance(transcript.text()) + 20)
                self.assertGreaterEqual(transcript.height(), transcript.fontMetrics().height() + 20)
                self.assertTrue(self.window.rect().contains(art))
                self.assertTrue(self.window.rect().contains(side))
                self.assertIs(view.info_panel.parentWidget(), self.window.house_sidebar_content)
                self.assertIs(self.window.weapon_action_panel.parentWidget(), self.window.house_sidebar_content)
                self.assertLessEqual(self.window.message_log.height(), 128)
                view.buttons['library'].setFocus()
                self.app.processEvents()
                self.assertIn('LIBRARY', view.info_title.text())

    def test_action_open_initializes_native_room_masks_without_event_dispatch(self):
        self.window.player_name_input.setText("Alex")
        self.window.start_game()
        bare = next(w for w in self.window.weapons if w['id'] == 'bare')
        self.window.select_weapon_and_start(bare)
        view = self.window.house_view
        self.assertGreater(view.width(), 1)
        for room_id, point in (("attic", (530, 250)), ("kitchen", (250, 800)),
                               ("library", (990, 760)), ("basement", (285, 1060)),
                               ("cellar", (1012, 1030))):
            mapped = view.image_transform.map(QPointF(*point)).toPoint()
            button = view.buttons[room_id]
            self.assertTrue(button.hitButton(button.mapFrom(view, mapped)))
            self.assertIs(view.childAt(mapped), button)

    def test_native_masked_click_and_keyboard_use_existing_room_dispatch(self):
        self.start_night()
        view = self.window.house_view
        with patch.object(self.window, 'offer_room_item') as offer:
            for room_id, point in (('attic', (530,250)), ('library',(990,760)), ('cellar',(1012,1030))):
                target = view.image_transform.map(QPointF(*point)).toPoint()
                button = view.childAt(target)
                self.assertIs(button, view.buttons[room_id])
                QTest.mouseClick(button, Qt.LeftButton, pos=button.mapFrom(view, target))
                self.assertEqual(offer.call_args.args[0], view.buttons[room_id].text())
            button = view.buttons['kitchen']
            button.setFocus()
            QTest.keyClick(button, Qt.Key_Return)
            self.assertEqual(offer.call_args.args[0], 'Kitchen')
            offer.reset_mock()
            self.window.old_crime_scenes = ['Library']
            self.window.set_room_buttons_enabled(True)
            view.buttons['library'].click()
            self.window.choose_room('Library')
            offer.assert_not_called()

    def test_normal_projection_replaces_live_dump_and_retains_transcript(self):
        self.start_night()
        self.assertNotIn('Room Assignments;', self.window.output_area.toPlainText())
        self.assertTrue(any('Room Assignments;' in r.text for r in self.window.history_records))
        for key, record in self.window.house_view.observations.items():
            room = self.window.house_view.buttons[key].text()
            self.assertEqual(record.known_names, tuple(self.window.room_assignments[room]))
            self.assertFalse(record.unknown_remainder)
            self.assertIsNone(record.light_on)
            self.assertIsNone(record.observed_at)

    def test_hard_projection_accessibility_and_render_ignore_hidden_assignments(self):
        self.start_night(normal=False)
        view = self.window.house_view
        view.buttons['library'].setFocus()
        self.app.processEvents()
        before = view.grab().toImage()
        descriptions = [button.accessibleDescription() for button in view.buttons.values()]
        for room in self.window.rooms:
            self.window.room_assignments[room] = ['Private sentinel ' + room]
        self.window.update_room_button_states()
        self.assertEqual(view.grab().toImage(), before)
        self.assertEqual([button.accessibleDescription() for button in view.buttons.values()], descriptions)
        self.assertTrue(all(not r.known_names and r.unknown_remainder for r in view.observations.values()))
        self.assertNotIn('sentinel', view.info_body.text())

    def test_known_empty_resolution_is_public_but_hover_does_not_reveal(self):
        self.start_night(normal=False)
        self.window.room_assignments['Cellar'] = []
        self.window.house_view.buttons['cellar'].setFocus()
        self.assertTrue(self.window.house_view.observations['cellar'].unknown_remainder)
        self.window.finish_room_choice('Cellar')
        self.assertFalse(self.window.house_view.observations['cellar'].unknown_remainder)
        self.assertFalse(self.window.room_buttons['Cellar'].isEnabled())
        self.assertIn('empty', self.window.output_area.toPlainText())
        self.assertTrue(self.window.room_buttons_widget.isVisible())

    def test_occupied_target_choice_reveals_only_accepted_room_and_resets_next_night(self):
        self.start_night(normal=False)
        self.window.room_assignments['Kitchen'] = ['Jerry', 'Dave']
        self.window.finish_room_choice('Kitchen')
        self.assertEqual(self.window.house_view.observations['kitchen'].known_names, ('Jerry', 'Dave'))
        self.assertFalse(self.window.house_view.observations['kitchen'].unknown_remainder)
        self.assertTrue(self.window.house_view.observations['library'].unknown_remainder)
        self.window.victim_buttons_widget.hide()
        self.window.run_action_phase()
        self.assertIsNone(self.window.house_view.selected_room)
        self.assertIsNone(self.window.house_view.info_room)
        self.assertNotIn('Jerry', self.window.house_view.info_body.text())
        self.assertTrue(all(not r.known_names and r.unknown_remainder for r in self.window.house_view.observations.values()))
        with patch.object(self.window, 'run_conversation_loop'):
            self.window.run_conversation_phase()
        self.assertEqual(self.window.message_log.maximumHeight(), 16777215)

    def test_reward_continuation_and_stale_callback_preserve_house_boundary(self):
        from items import DraftState
        self.start_night(normal=False)
        self.window.item_drafts = DraftState(self.window.player_owner_id, ("banana", "muffle", "spare_paper"))
        self.window.room_assignments['Cellar'] = []
        self.window.choose_room('Cellar')
        offer = self.window.item_drafts.pending_offer
        page = self.window.draft_page
        self.assertIsNotNone(offer)
        self.assertTrue(self.window.room_buttons_widget.isHidden())
        self.assertTrue(self.window.weapon_action_panel.isHidden())
        self.assertEqual(self.window.layout.indexOf(page) + 1,
                         self.window.layout.indexOf(self.window.room_buttons_widget))
        self.assertTrue(self.window.house_view.observations['cellar'].unknown_remainder)
        page.leave_button.click()
        self.assertIsNone(self.window.pending_room_action)
        self.assertTrue(self.window.room_buttons_widget.isVisible())
        self.assertFalse(self.window.house_view.observations['cellar'].unknown_remainder)
        self.assertFalse(self.window.room_buttons['Cellar'].isEnabled())
        self.assertTrue(self.window.room_buttons['Library'].isEnabled())
        before = tuple(self.window.rooms_visited)
        self.window.skip_item_offer(offer.offer_id)
        self.window.choose_item_offer(offer.offer_id, 0)
        self.assertEqual(tuple(self.window.rooms_visited), before)

    def test_action_weapon_description_uses_readable_body_font(self):
        import main
        self.start_night()
        for weapon_id, extra in (("bare", None), ("knife", "Jerry")):
            self.window.selected_weapon = next(w for w in self.window.weapons if w['id'] == weapon_id)
            self.window.knife_extra_target = extra
            self.window.build_weapon_action_panel()
            self.app.processEvents()
            description = self.window.weapon_action_panel.findChild(QLabel, "actionWeaponDescription")
            name = self.window.weapon_action_panel.findChild(QLabel, "actionWeaponName")
            self.assertEqual(QFontInfo(description.font()).family(), QFontInfo(main.readable_body_font()).family())
            self.assertEqual(description.font().pixelSize(), 18)
            self.assertEqual(QFontInfo(name.font()).family(), self.window.pixel_font_family)

    def test_victory_directly_from_action_restores_expanded_visible_result(self):
        self.start_night()
        self.window.resize(800, 600)
        QTest.qWait(10)
        self.assertEqual(self.window.phase, "action")
        self.assertEqual(self.window.message_log.maximumHeight(), 128)
        for name, character in self.window.characters.items():
            character['alive'] = name == 'Jerry'
        self.assertTrue(self.window.check_victory())
        QTest.qWait(10)
        self.assertEqual(self.window.phase, "game_over")
        self.assertTrue(self.window.game_over)
        self.assertEqual(self.window.message_log.maximumHeight(), 16777215)
        self.assertGreater(self.window.message_log.height(), 128)
        self.assertTrue(self.window.room_buttons_widget.isHidden())
        score = self.window.output_area.document().find("Final Score: 4/5")
        self.assertFalse(score.isNull())
        self.assertTrue(self.window.output_area.viewport().rect().contains(
            self.window.output_area.cursorRect(score)))

    def test_artwork_resource_is_exact_reviewed_asset(self):
        import main
        path = Path(main.resource_path('House_images/house-cutaway-v1.png'))
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         '8fff5c8556870953d6e4c5d2934443a9491856339c14b9ef15c71606266610f0')


# Avoid collecting the imported fixture class a second time.
del RuntimeRegressionTests

if __name__ == '__main__':
    unittest.main()
