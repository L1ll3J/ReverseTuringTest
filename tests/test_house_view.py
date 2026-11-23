"""Offline native interaction and public-projection checks for the cutaway."""

from dataclasses import replace
import os
from pathlib import Path
import socket
import subprocess
import sys
import unittest
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PyQt5.QtCore import QCoreApplication, QEvent, QPoint, QPointF, Qt
from PyQt5.QtGui import QKeyEvent, QPixmap
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import QApplication

from house_view import ARTWORK_SIZE, HouseView, RoomButton, RoomObservation, observation_text, unknown_observations
from design.house_study import HouseStudy


class HouseViewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["house-view-tests"])
        cls.app.setQuitOnLastWindowClosed(False)
        cls.artwork = ROOT / "design/house-cutaway-v1.png"

    def setUp(self):
        self.view = HouseView(self.artwork)
        self.view.resize(1280, 800)
        self.view.show()
        self.view.activateWindow()
        QTest.qWait(10)

    def tearDown(self):
        self.view.close()
        self.view.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def at(self, x, y):
        return self.view.image_transform.map(QPointF(x, y)).toPoint()

    def click_image(self, x, y):
        point = self.at(x, y)
        child = self.view.childAt(point)
        if isinstance(child, RoomButton):
            QTest.mouseClick(child, Qt.LeftButton, pos=child.mapFrom(self.view, point))
        else:
            QTest.mouseClick(self.view, Qt.LeftButton, pos=point)

    def image_pixels(self):
        image = self.view.grab().toImage().copy(self.view.image_rect.toAlignedRect())
        return image.bits().asstring(image.byteCount())

    def test_uniform_fit_is_centred_bounded_and_clickable_at_every_target_size(self):
        for width, height in ((800, 600), (1280, 800), (2560, 1440), (3440, 1440)):
            with self.subTest(size=(width, height)):
                self.view.resize(width, height)
                self.app.processEvents()
                rect = self.view.image_rect
                transform = self.view.image_transform
                self.assertAlmostEqual(transform.m11(), transform.m22(), places=12)
                self.assertAlmostEqual(rect.width() / rect.height(), ARTWORK_SIZE[0] / ARTWORK_SIZE[1], places=12)
                self.assertAlmostEqual(rect.center().x(), width / 2)
                self.assertAlmostEqual(rect.center().y(), height / 2)
                self.assertGreaterEqual(rect.left(), 0)
                self.assertGreaterEqual(rect.top(), 0)
                self.assertLessEqual(rect.right(), width)
                self.assertLessEqual(rect.bottom(), height)
                self.assertEqual(self.view.room_at(self.at(780, 780)), "library")
                self.assertEqual(self.view.room_at(self.at(1012, 1030)), "cellar")
                self.assertEqual(self.view.room_at(self.at(520, 770)), None)

    def test_roof_bay_vault_and_exterior_door_use_real_native_hit_regions(self):
        points = {
            "attic": ((520, 500), (530, 250)),
            "kitchen": ((250, 800),),
            "library": ((780, 780), (990, 760)),
            "basement": ((285, 1060),),
            "cellar": ((760, 1060), (1012, 1030)),
        }
        selected = QSignalSpy(self.view.room_selected)
        for room_id, coordinates in points.items():
            for point in coordinates:
                with self.subTest(room=room_id, point=point):
                    self.assertEqual(self.view.room_at(self.at(*point)), room_id)
                    self.assertIs(self.view.childAt(self.at(*point)), self.view.buttons[room_id])
                    before = len(selected)
                    self.click_image(*point)
                    self.assertEqual(len(selected), before + 1)
                    self.assertEqual(list(selected[-1]), [room_id])

    def test_hall_stair_front_door_grounds_and_shape_corners_are_not_buttons(self):
        selected = QSignalSpy(self.view.room_selected)
        for point in ((520, 770), (520, 1060), (540, 1290), (300, 100),
                      (185, 225), (250, 310), (115, 200), (626, 950), (950, 1090)):
            with self.subTest(point=point):
                self.assertIsNone(self.view.room_at(self.at(*point)))
                self.assertNotIsInstance(self.view.childAt(self.at(*point)), RoomButton)
                self.click_image(*point)
        self.assertEqual(len(selected), 0)

    def test_native_accessibility_names_and_tab_chain_cover_five_rooms(self):
        self.assertEqual(len(self.view.buttons), 5)
        attic = self.view.buttons["attic"]
        attic.setFocus()
        for expected in ("Attic", "Kitchen", "Library", "Basement", "Cellar"):
            focused = QApplication.focusWidget()
            self.assertIsInstance(focused, RoomButton)
            self.assertEqual(focused.accessibleName(), expected)
            self.assertEqual(focused.text(), expected)
            QTest.keyClick(focused, Qt.Key_Tab)

    def test_arrow_navigation_is_spatial_and_skips_unavailable_rooms(self):
        kitchen = self.view.buttons["kitchen"]
        kitchen.setFocus()
        QTest.keyClick(kitchen, Qt.Key_Right)
        self.assertTrue(self.view.buttons["library"].hasFocus())
        QTest.keyClick(self.view.buttons["library"], Qt.Key_Down)
        self.assertTrue(self.view.buttons["cellar"].hasFocus())
        records = tuple(replace(record, available=False) if record.room_id == "cellar" else record
                        for record in unknown_observations())
        self.view.set_observations(records)
        self.view.buttons["library"].setFocus()
        QTest.keyClick(self.view.buttons["library"], Qt.Key_Down)
        self.assertFalse(self.view.buttons["cellar"].hasFocus())
        self.assertFalse(self.view.buttons["cellar"].isEnabled())

    def test_enter_keypad_and_space_emit_once_and_return_autorepeat_does_not_select(self):
        button = self.view.buttons["library"]
        button.setFocus()
        selected = QSignalSpy(self.view.room_selected)
        for key, modifiers in ((Qt.Key_Return, Qt.NoModifier), (Qt.Key_Enter, Qt.NoModifier),
                               (Qt.Key_Enter, Qt.KeypadModifier), (Qt.Key_Space, Qt.NoModifier)):
            before = len(selected)
            QTest.keyClick(button, key, modifiers)
            self.assertEqual(len(selected), before + 1)
        before = len(selected)
        QApplication.sendEvent(button, QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier, "", True, 2))
        QApplication.sendEvent(button, QKeyEvent(QEvent.KeyRelease, Qt.Key_Return, Qt.NoModifier, "", True, 2))
        self.assertEqual(len(selected), before)

    def test_unavailable_room_cannot_emit_mouse_or_keyboard_selection(self):
        records = tuple(replace(record, available=False) if record.room_id == "library" else record
                        for record in unknown_observations())
        self.view.set_observations(records)
        selected = QSignalSpy(self.view.room_selected)
        self.click_image(780, 780)
        button = self.view.buttons["library"]
        QTest.keyClick(button, Qt.Key_Return)
        button.click()
        self.assertEqual(len(selected), 0)

    def test_focus_and_hover_reveal_the_same_literal_public_information(self):
        observation = RoomObservation("library", known_names=("Jerry", "<b>Dave</b>"),
                                      unknown_remainder=True, observed_at="00:40", light_on=True)
        records = tuple(observation if record.room_id == "library" else record for record in unknown_observations())
        self.view.set_observations(records)
        self.view.buttons["library"].setFocus()
        self.assertEqual(self.view.info_title.text(), "LIBRARY")
        self.assertIn("Others may be present.", self.view.info_body.text())
        self.assertIn("<b>Dave</b>", self.view.info_body.text())
        self.assertEqual(self.view.info_body.textFormat(), Qt.PlainText)
        expected = self.view.info_body.text()
        self.view.buttons["attic"].setFocus()
        QApplication.sendEvent(self.view.buttons["library"], QEvent(QEvent.Enter))
        self.assertEqual(self.view.info_body.text(), expected)
        self.assertEqual(self.view.buttons["library"].accessibleDescription(), expected)

    def test_unknown_never_becomes_empty_and_observed_names_do_not_close_unknown_remainder(self):
        unknown = RoomObservation("kitchen")
        self.assertIn("Occupants unknown", observation_text(unknown))
        self.assertNotIn("empty", observation_text(unknown).lower())
        partial = RoomObservation("kitchen", known_names=("Jerry",), unknown_remainder=True)
        self.assertIn("Others may be present", observation_text(partial))
        complete = replace(partial, known_names=(), unknown_remainder=False, observed_at="00:30")
        self.assertIn("No one was observed", observation_text(complete))
        self.assertIn("00:30", observation_text(complete))

    def test_equal_public_projections_have_equal_rendering_and_accessibility(self):
        # Different hidden worlds are intentionally not an input to this component.
        first = tuple(RoomObservation(record.room_id) for record in unknown_observations())
        second = tuple(replace(record) for record in first)
        self.view.buttons["library"].setFocus()
        self.view.set_observations(first)
        before = self.view.grab().toImage()
        descriptions = tuple(button.accessibleDescription() for button in self.view.buttons.values())
        self.view.set_observations(second)
        self.assertEqual(self.view.grab().toImage(), before)
        self.assertEqual(tuple(button.accessibleDescription() for button in self.view.buttons.values()), descriptions)
        with self.assertRaises(TypeError):
            self.view.set_observations({"actual_occupants": ("Hidden sentinel",)})
        with self.assertRaises(TypeError):
            RoomObservation("library", actual_count=4)

    def test_names_cannot_change_room_lighting_but_an_observed_light_can(self):
        self.view.buttons["attic"].setFocus()
        base = unknown_observations()
        self.view.set_observations(base)
        before = self.image_pixels()
        known = tuple(replace(record, known_names=("Jerry", "Dave")) if record.room_id == "library" else record
                      for record in base)
        self.view.set_observations(known)
        self.assertEqual(self.image_pixels(), before)
        lit = tuple(replace(record, light_on=True) if record.room_id == "library" else record for record in known)
        self.view.set_observations(lit)
        self.assertNotEqual(self.image_pixels(), before)

    def test_projection_validation_is_atomic(self):
        before = dict(self.view.observations)
        for invalid in ((RoomObservation("attic"),), (*unknown_observations()[:-1], RoomObservation("attic"))):
            with self.assertRaises(ValueError):
                self.view.set_observations(invalid)
            self.assertEqual(self.view.observations, before)
        for fields in ({"available": 1}, {"unknown_remainder": 0}, {"light_on": "on"},
                       {"known_names": "Dave"}, {"known_names": ("Dave", "Dave")}):
            with self.subTest(fields=fields), self.assertRaises((TypeError, ValueError)):
                RoomObservation("library", **fields)

    def test_wrong_artwork_dimensions_are_rejected(self):
        with self.assertRaises(ValueError):
            HouseView(QPixmap(300, 300))

    def test_high_dpi_process_keeps_one_scale_and_coordinate_mapping(self):
        script = """
from pathlib import Path
from PyQt5.QtCore import QPointF, Qt
from PyQt5.QtWidgets import QApplication
from house_view import HouseView
QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
app=QApplication([])
view=HouseView(Path('design/house-cutaway-v1.png'))
view.resize(1280,800)
view.show()
app.processEvents()
assert view.devicePixelRatioF() > 1
assert view.image_transform.m11() == view.image_transform.m22()
assert view.room_at(view.image_transform.map(QPointF(990,760))) == 'library'
assert view.room_at(view.image_transform.map(QPointF(520,770))) is None
view.close()
"""
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR="1.5")
        result = subprocess.run([sys.executable, "-c", script], cwd=ROOT, env=env,
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


class HouseStudyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication(["house-study-tests"])
        cls.app.setQuitOnLastWindowClosed(False)

    def setUp(self):
        self.network = patch.object(socket.socket, "connect", side_effect=AssertionError("Offline study"))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.window = HouseStudy(ROOT / "design/house-cutaway-v1.png", ROOT)
        self.window.show()
        self.window.activateWindow()
        QTest.qWait(10)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_native_projection_choice_shows_one_known_name_and_unknown_remainder(self):
        choice = self.window.projection
        choice.setFocus()
        QTest.keyClick(choice, Qt.Key_Down)
        self.assertEqual(choice.currentIndex(), 1)
        record = self.window.house.observations["library"]
        self.assertEqual(record.known_names, ("Jerry",))
        self.assertTrue(record.unknown_remainder)
        self.window.house.buttons["library"].setFocus()
        self.assertIn("Others may be present", self.window.house.info_body.text())

    def test_selection_is_a_signal_and_does_not_enter_a_room_or_change_observations(self):
        view = self.window.house
        before = dict(view.observations)
        selected = QSignalSpy(view.room_selected)
        point = view.image_transform.map(QPointF(780, 780)).toPoint()
        button = view.buttons["library"]
        QTest.mouseClick(button, Qt.LeftButton, pos=button.mapFrom(view, point))
        self.assertEqual(list(selected[0]), ["library"])
        self.assertEqual(view.observations, before)
        self.assertIn("No entry action", self.window.status.text())

    def test_compact_helper_and_artwork_remain_inside_every_window_size(self):
        for width, height in ((800, 600), (1280, 800), (2560, 1440), (3440, 1440)):
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.window.projection.setCurrentIndex(1)
                self.window.house.buttons["library"].setFocus()
                self.app.processEvents()
                view = self.window.house
                self.assertEqual((self.window.width(), self.window.height()), (width, height))
                self.assertTrue(view.rect().contains(view.info_panel.geometry()))
                self.assertLessEqual(view.info_panel.width(), 238)
                self.assertLessEqual(view.info_panel.geometry().right(), view.image_rect.left())

    def test_narrow_observation_helper_has_room_for_every_wrapped_line(self):
        self.window.projection.setCurrentIndex(1)
        for width, height in ((800, 600), (1280, 800), (800, 600)):
            self.window.resize(width, height)
            self.app.processEvents()
            for room_id in ("library", "cellar"):
                with self.subTest(size=(width, height), room=room_id):
                    view = self.window.house
                    QApplication.sendEvent(view.buttons[room_id], QEvent(QEvent.Enter))
                    self.app.processEvents()
                    body = view.info_body
                    required_height = body.heightForWidth(body.width())
                    self.assertGreaterEqual(body.height(), required_height)
                    self.assertTrue(view.info_panel.rect().contains(body.geometry()))
                    self.assertTrue(view.rect().contains(view.info_panel.geometry()))
                    if room_id == "library":
                        self.assertIn("Light observed on.", body.text())


if __name__ == "__main__":
    unittest.main()
