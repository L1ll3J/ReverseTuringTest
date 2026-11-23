"""Night blast summaries reflect resolved deaths rather than room occupancy."""

from unittest.mock import patch
import unittest

import test_runtime_regressions as runtime


class BlastReportingTests(unittest.TestCase):
    setUpClass = runtime.RuntimeRegressionTests.__dict__["setUpClass"]
    setUp = runtime.RuntimeRegressionTests.setUp
    tearDown = runtime.RuntimeRegressionTests.tearDown

    def test_protected_survivor_is_not_listed_among_blast_victims(self):
        window = self.window
        window.phase = "conversation"
        window.round_number = 1
        window.ensure_identity_state()
        guardian = window.inventory.grant("guardian_angel")
        self.assertTrue(window.use_inventory_item(guardian.instance_id))
        window.exchange_player_mind(window.body_id("Dave"))
        window.phase = "action"
        window.selected_weapon = next(weapon for weapon in window.weapons if weapon["id"] == "c4")
        window.c4_target_room = "Attic"
        window.room_assignments = {room: [] for room in window.rooms}
        window.room_assignments["Attic"] = ["Alex", "Jerry"]
        window.room_assignments["Kitchen"] = ["Hank"]
        window.rooms_visited = []
        window.new_crime_scenes = []
        window.room_buttons_widget.show()
        with patch.object(window, "prepare_room_entry", return_value=True), patch.object(window, "finish_night"):
            window.finish_room_choice("Kitchen")
        self.assertTrue(window.characters["Alex"]["alive"])
        self.assertFalse(window.characters["Jerry"]["alive"])
        summaries = [record.text for record in window.history_records if "C4 detonates" in record.text]
        self.assertEqual(summaries, ["The C4 detonates in the Attic. It kills: Jerry.\n"])


if __name__ == "__main__":
    unittest.main()
