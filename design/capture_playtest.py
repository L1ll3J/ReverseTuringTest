"""Capture native Qt screens with isolated fixtures, no network or microphone use."""

import argparse
from contextlib import ExitStack
import os
from pathlib import Path
import socket
import sys
import tempfile
from time import perf_counter
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform == "win32":
    os.environ.setdefault("QT_QPA_FONTDIR", "C:/Windows/Fonts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtGui import QFontDatabase, QFontInfo
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

import main as game
from items import DraftState
from progression import ProgressEvent


def capture(output):
    output.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication(["AlibAi offline screen capture"])
    app.setQuitOnLastWindowClosed(False)
    if sys.platform == "win32" and app.platformName() == "offscreen":
        QFontDatabase.addApplicationFont("C:/Windows/Fonts/segoeui.ttf")

    def save(widget, name):
        app.processEvents()
        QTest.qWait(30)
        if not widget.grab().save(str(output / (name + ".png"))):
            raise OSError("Could not save screen capture")

    with tempfile.TemporaryDirectory() as directory, ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {
            "OPENAI_API_KEY": "", "ALIBAI_DEV_MODE": "0",
            "ALIBAI_PROFILE_PATH": str(Path(directory) / "profile.json"),
            "ALIBAI_SETTINGS_PATH": str(Path(directory) / "settings.ini"),
        }))
        stack.enter_context(patch.object(socket.socket, "connect", side_effect=AssertionError("Offline capture")))
        stack.enter_context(patch.object(game, "QMediaPlayer"))
        stack.enter_context(patch.object(game.GameWindow, "maybe_prepare_next_turn"))
        stack.enter_context(patch.object(game.GameWindow, "run_conversation_loop"))
        window = game.GameWindow()
        try:
            window.showNormal()
            for width, height in ((1280, 800), (1920, 1080), (2560, 1440)):
                window.resize(width, height)
                save(window, f"menu-{width}")
                start = perf_counter()
                window.items_tab_button.click()
                print(f"Items switch at {width}: {(perf_counter() - start) * 1000:.1f} ms")
                save(window, f"items-{width}")
                window.play_tab_button.click()
            window.player_name_input.setText("Alex")
            window.profile_store.record(ProgressEvent("capture", 1, "player_kill"))
            window.load_profile()
            with patch.object(game.NPCItemState, "allocate", return_value=game.NPCItemState({"Dave": "camera"})):
                window.start_game()
            save(window, "weapons-2560")
            knife = next(weapon for weapon in window.weapons if weapon["id"] == "knife")
            with patch.object(game.random, "choice", side_effect=lambda choices: choices[0]):
                window.select_weapon_and_start(knife)
            save(window, "house-2560")
            window.open_knife_dialog()
            save(window._weapon_windows["knife"], "knife-editor")
            window._weapon_windows["knife"].close()
            window.item_drafts = DraftState(window.player_owner_id, ("banana", "muffle", "spare_paper"))
            window.choose_room("Attic")
            save(window, "room-offer-2560")
            offer = window.item_drafts.pending_offer
            window.draft_page.take_buttons[offer.options.index("banana")].click()
            window.kill_victim("Jerry")
            if window.phase != "conversation":
                raise AssertionError("The capture fixture did not reach discussion")
            window.append_speech("Hank", "Dave, I saw you watching the stairs. What did you see?", "Dave")
            window.interject_input.show()
            window.say_button.show()
            window.update_recipient_controls()
            for width, height in ((1280, 800), (1920, 1080), (2560, 1440)):
                window.resize(width, height)
                save(window, f"conversation-{width}")
            window.show_transcript()
            save(window.overlay_host, "transcript")
            window.overlay_host.close_page()
            window.open_inventory()
            save(window.overlay_host, "inventory")
            window.overlay_host.close_page()
            window.show_pause_menu_page()
            save(window.overlay_host, "pause")
            window.close_escape_menu()
            window.show_microphone_settings()
            save(window.overlay_host, "microphone")
            window.close_escape_menu()
            print("Body font:", QFontInfo(game.readable_body_font()).family())
            print("Captures:", output)
        finally:
            window.close()
            window.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(tempfile.gettempdir()) / "alibai-full-ui-captures")
    capture(parser.parse_args().output)
