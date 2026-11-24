"""Offline production arrival ordering, private observations and settled camera facts."""
from dataclasses import replace
import unittest
from unittest.mock import Mock, patch

from test_item_integration import ItemIntegrationTests
import main as game
from items import DraftState
from night_arrivals import NightArrivals
from npc_items import NPCItemState


class ArrivalRng:
    def shuffle(self, values):
        pass

    def randint(self, low, high):
        return low


class NightArrivalIntegrationTests(unittest.TestCase):
    setUpClass = ItemIntegrationTests.__dict__['setUpClass']
    setUp = ItemIntegrationTests.setUp
    tearDown = ItemIntegrationTests.tearDown
    weapon = ItemIntegrationTests.weapon
    conversation = ItemIntegrationTests.conversation
    arm_optional = ItemIntegrationTests.arm_optional

    def open_night(self, *, initial_count=1, normal=True, plan=None, actual_assignment=False):
        self.window.show_room_allocations = normal
        plan = plan or {'Hank':'Kitchen', 'Dave':'Kitchen', 'Jerry':'Attic', 'Debra':'Library', 'Owen':'Cellar'}
        placements = {self.window.body_id(name): room for name, room in plan.items()}
        self.window.selected_weapon = self.weapon()
        assignment_patch = ({'wraps': game.assign_guest_rooms} if actual_assignment else {'return_value': placements})
        with patch.object(game, 'assign_guest_rooms', **assignment_patch), patch.object(
                game, 'NightArrivals', side_effect=lambda night, assigned: NightArrivals(
                    night, assigned, rng=ArrivalRng(), initial_count=initial_count)):
            if self.window.phase == "weapon_select":
                self.window.select_weapon_and_start(self.weapon())
            else:
                self.window.weapon_scroll.hide()
                self.window.run_action_phase()
        self.window.item_drafts = DraftState(self.window.player_owner_id, ('banana','muffle','spare_paper'))
        self.window.show()
        self.app.processEvents()
        return self.window.night_arrivals

    def test_normal_initial_subset_and_hard_unknown_projection_without_count_leak(self):
        state = self.open_night(initial_count=2)
        self.assertEqual(sum(map(len, self.window.room_assignments.values())), 2)
        self.assertEqual(self.window.house_view.observations['kitchen'].known_names, ('Hank','Dave'))
        self.assertEqual(self.window.arrival_clock.text(), '19:45')
        self.assertFalse(self.window.npc_items._photos_by_night)
        self.window.show_room_allocations = False
        state = self.open_night(initial_count=1, normal=False)
        self.assertEqual(self.window.arrival_clock.text(), '19:45')
        self.assertTrue(all(not fact.known_names and fact.unknown_remainder
                            for fact in self.window.house_view.observations.values()))
        self.assertNotIn('Hank', '\n'.join(record.text for record in self.window.history_records
                                          if record.round_number == state.night))
        self.assertNotIn('1', self.window.arrival_status.text())

    def test_loiter_advances_clock_reveals_one_guest_and_private_observation_only_once(self):
        state = self.open_night(normal=False)
        cursor = state.cursor
        event = state.events[cursor]
        self.window.loiter_button.click()
        self.assertEqual(state.cursor, cursor + 1)
        self.assertEqual(self.window.arrival_clock.text(), event.time)
        self.assertEqual(self.window.house_view.observations['kitchen'].known_names, ('Dave',))
        memories = self.window.characters['Dave']['experiences']
        expected = f'At {event.time}, you saw Alex outside the house as you went through the front door towards the kitchen. They glanced towards you.'
        own_entry = f'At {event.time}, you entered the Kitchen.'
        self.assertEqual([record.text for record in memories], [own_entry, expected])
        self.window.loiter_outside(state.night, cursor)
        self.assertEqual(state.cursor, cursor + 1)
        self.assertEqual([record.text for record in memories], [own_entry, expected])
        first = state.initial[0]
        self.assertEqual([record.text for record in self.window.characters['Hank']['experiences']],
                         [f'At {first.time}, you entered the Kitchen.'])
        self.assertFalse(self.window.characters['Jerry']['experiences'])

    def test_initial_guests_remember_their_actual_entry_in_both_modes(self):
        for normal in (True, False):
            with self.subTest(normal=normal):
                state = self.open_night(initial_count=2, normal=normal)
                for event in state.initial:
                    mind_id = self.window.identity_state.mind_for_body(event.body_id)
                    memories = [record for record in self.window.mind_data[mind_id]['experiences']
                                if record.round_number == state.night]
                    self.assertEqual([record.text for record in memories],
                                     [f'At {event.time}, you entered the {event.room}.'])
                    self.assertEqual(memories[0].source, 'eyewitness')
                    self.window.apply_arrival(event, observed=normal)
                    self.assertEqual(len([record for record in self.window.mind_data[mind_id]['experiences']
                                          if record.round_number == state.night]), 1)

    def test_room_commits_before_rewards_and_camera_contains_final_human_room_before_attack(self):
        self.window.npc_items = NPCItemState({'Hank':'camera'})
        state = self.open_night(normal=False)
        self.window.choose_room('Kitchen')
        self.assertEqual(state.committed_room, 'Kitchen')
        self.assertTrue(state.complete)
        self.assertIsNotNone(self.window.pending_room_action)
        self.assertEqual(self.window.night_attacks, [])
        photo, = self.window.npc_items._photos_by_night[state.night]
        self.assertEqual(set(photo.occupants), {'Alex','Dave','Hank'})
        self.assertEqual(set(photo.occupant_body_ids), {self.window.body_id(name) for name in photo.occupants})
        self.assertEqual(photo.capture_stage, 'before_player_action')
        self.assertTrue(self.window.house_view.observations['attic'].unknown_remainder)
        self.assertFalse(self.window.house_view.observations['attic'].known_names)
        transcript = '\n'.join(record.text for record in self.window.history_records)
        self.assertNotIn('Jerry entered', transcript)
        event = next(event for event in state.events if event.body_id == self.window.body_id('Jerry'))
        memories = self.window.characters['Jerry']['experiences']
        self.assertEqual([record.text for record in memories],
                         [f'At {event.time}, you entered the Attic.'])
        self.window.loiter_outside(state.night, 1)
        self.window.apply_arrival(event)
        self.assertEqual(len(memories), 1)
        self.window.draft_page.leave_button.click()
        self.assertIs(self.window.npc_items._photos_by_night[state.night][0], photo)
        self.assertEqual(self.window.night_attacks, [])

    def test_human_possessing_camera_body_cannot_take_automatic_npc_photo(self):
        self.conversation()
        self.window.npc_items = NPCItemState({'Hank': 'camera'})
        camera_body = self.window.body_id('Hank')
        self.window.exchange_player_mind(camera_body)
        self.window.selected_weapon = self.weapon()
        self.window.run_action_phase()
        self.assertTrue(self.window.prepare_room_entry('Kitchen'))
        self.assertEqual(self.window.player_name, 'Hank')
        self.assertEqual(self.window.npc_items._photos_by_night[self.window.round_number], ())
        self.assertEqual(dict(self.window.npc_items.equipment), {'Hank': 'camera'})

    def test_solo_trap_then_flight_tells_only_actual_copresent_guest_and_camera_settles(self):
        self.conversation()
        self.arm_optional('booby_trap', room='Attic')
        self.window.npc_items = NPCItemState({'Hank':'camera'})
        state = self.open_night(initial_count=2, normal=False, plan={
            'Dave':'Attic', 'Hank':'Kitchen', 'Jerry':'Attic', 'Debra':'Library', 'Owen':'Cellar'})
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertNotIn('Dave', '\n'.join(record.text for record in self.window.history_records
                                          if record.round_number == state.night))
        with patch.object(game, 'flight_destination', return_value=('Kitchen', ())):
            self.window.choose_room('Kitchen')
        self.assertTrue(self.window.characters['Jerry']['alive'])
        self.assertEqual(len(self.window.item_deaths), 1)
        self.assertEqual(self.window.room_assignments['Attic'], ['Dave'])
        self.assertEqual(self.window.room_assignments['Kitchen'], ['Hank','Jerry'])
        discoverer = self.window.characters['Jerry']['experiences']
        recipient = self.window.characters['Hank']['experiences']
        self.assertEqual(len(discoverer), 2)
        self.assertEqual(discoverer[1].source, 'eyewitness')
        self.assertIn('you recognised Dave; their body had been torn apart.', discoverer[1].text)
        self.assertIn('hurried to the Kitchen.', discoverer[1].text)
        self.assertEqual(len(recipient), 2)
        self.assertEqual(recipient[1].source, 'hearsay')
        self.assertIn('Jerry told you what they had found in the Attic.', recipient[1].text)
        for name in ('Debra', 'Owen'):
            event = next(event for event in state.events if event.body_id == self.window.body_id(name))
            self.assertEqual([record.text for record in self.window.characters[name]['experiences']],
                             [f'At {event.time}, you entered the {event.room}.'])
        photo, = self.window.npc_items._photos_by_night[state.night]
        self.assertEqual(set(photo.occupants), {'Alex','Hank','Jerry'})
        self.assertNotIn('Dave', photo.occupants)
        self.assertFalse(self.window.house_view.observations['attic'].known_names)
        self.assertNotIn('Dave', self.window.night_weapon_targets())
        event = state.events[2]
        before = (len(discoverer), len(recipient))
        self.window.apply_arrival(event)
        self.assertEqual((len(discoverer), len(recipient)), before)
        captured_mind = self.window.mind_id('Hank')
        captured_memories = self.window.mind_data[captured_mind]['experiences']
        self.window.exchange_player_mind(self.window.body_id('Hank'))
        self.assertIs(self.window.characters['Alex']['experiences'], captured_memories)
        self.assertIs(captured_memories[0], recipient[0])

    def test_capacity_pressure_reopens_oldest_minimum_before_reservations(self):
        self.window.old_crime_scenes = ['Kitchen','Attic','Basement','Library']
        state = self.open_night(actual_assignment=True)
        self.assertTrue(all(room in self.window.night_rooms for room in state.destinations.values()))
        self.assertTrue(all(sum(value == room for value in state.destinations.values()) <= 2
                            for room in self.window.night_rooms))
        self.assertEqual(self.window.old_crime_scenes, ['Basement','Library'])
        self.assertEqual(self.window.night_rooms, ('Attic','Kitchen','Cellar'))
        self.assertTrue(self.window.room_buttons['Kitchen'].isEnabled())
        self.assertFalse(self.window.room_buttons['Basement'].isEnabled())

    def test_reservation_follows_its_mind_after_a_body_exchange(self):
        self.conversation()
        old_player_body = self.window.body_id('Alex')
        old_dave_body = self.window.body_id('Dave')
        entry = self.window.inventory.grant('room_reservation')
        self.assertTrue(self.window.use_inventory_item(entry.instance_id, old_dave_body, room_id='Attic'))
        self.window.exchange_player_mind(old_dave_body)
        self.window.selected_weapon = self.weapon()
        with patch.object(game, 'NightArrivals', side_effect=lambda night, assigned: NightArrivals(
                night, assigned, rng=ArrivalRng(), initial_count=1)):
            self.window.run_action_phase()
        self.assertEqual(self.window.night_arrivals.destinations[old_player_body], 'Attic')
        self.assertNotIn(old_dave_body, self.window.night_arrivals.destinations)
        self.assertIn('Alex', self.window.room_assignments['Attic'])
        event = next(event for event in self.window.night_arrivals.initial
                     if event.body_id == old_player_body)
        occupant_mind = self.window.identity_state.mind_for_body(old_player_body)
        expected = f'At {event.time}, you entered the Attic.'
        memories = self.window.mind_data[occupant_mind]['experiences']
        self.assertEqual([record.text for record in memories].count(expected), 1)
        self.assertNotEqual(occupant_mind, self.window.player_owner_id)
        self.assertNotIn(expected, [record.text for record in self.window.history_records])

    def test_due_curse_resolves_before_any_arrival_is_scheduled(self):
        self.conversation()
        self.arm_optional('occult_ritual', target='Dave')
        target = self.window.body_id('Dave')
        self.window.round_number = 2
        state = self.open_night(actual_assignment=True)
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertNotIn(target, state.destinations)
        self.assertFalse(any(event.body_id == target for event in state.events))
        self.assertEqual(self.window.night_attacks, [])

    def test_waiting_controls_remain_visible_in_sidebar_at_small_and_large_sizes(self):
        self.open_night(normal=False)
        for width, height in ((800, 600), (1280, 800), (2560, 1440)):
            self.window.resize(width, height)
            self.window.position_corner_controls()
            self.app.processEvents()
            viewport = self.window.house_sidebar.viewport()
            control = self.window.loiter_button
            point = control.mapTo(viewport, control.rect().topLeft())
            self.assertGreaterEqual(point.x(), 0)
            self.assertGreaterEqual(point.y(), 0)
            self.assertLessEqual(point.x() + control.width(), viewport.width())
            self.assertLessEqual(point.y() + control.height(), viewport.height())
            self.assertGreaterEqual(control.height(), 44)
            self.assertTrue(control.isEnabled())

    def hidden_dead_target_night(self, weapon_id):
        self.conversation()
        self.arm_optional('booby_trap', room='Attic')
        self.open_night(initial_count=1, normal=False, plan={
            'Dave':'Attic', 'Hank':'Kitchen', 'Jerry':'Library', 'Debra':'Basement', 'Owen':'Cellar'})
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertIn('Dave', self.window.night_weapon_targets())
        self.window.selected_weapon = self.weapon(weapon_id)
        self.window.build_weapon_action_panel()
        self.window.apply_weapon_gating()

    def check_hidden_blackmagic_plan(self, controller, target):
        self.hidden_dead_target_night('blackmagic')
        self.window.open_blackmagic_dialog()
        editor = self.window._weapon_windows['blackmagic']
        control_box, target_box = editor.findChildren(game.QComboBox)
        for box, name in ((control_box, controller), (target_box, target)):
            self.assertGreaterEqual(box.findData(self.window.body_id('Dave')), 0)
            box.setCurrentIndex(box.findData(self.window.body_id(name)))
        next(button for button in editor.findChildren(game.QPushButton) if button.text() == 'Save').click()
        self.assertTrue(editor.isHidden())
        self.assertEqual((self.window.black_magic_controller, self.window.black_magic_target), (controller, target))
        self.assertTrue(self.window.weapon_configured())
        self.assertTrue(self.window.room_buttons['Kitchen'].isEnabled())
        hidden_history = '\n'.join(record.text for record in self.window.history_records
                                   if record.round_number == self.window.round_number)
        self.assertNotIn('trap: Dave died.', hidden_history)
        self.assertNotIn('not powerful enough', hidden_history)
        self.window.choose_room('Kitchen')
        self.assertIsNotNone(self.window.pending_room_action)
        self.window.draft_page.leave_button.click()
        self.assertEqual(self.window.night_attacks, [])
        self.assertTrue(self.window.characters['Hank']['alive'])
        self.assertFalse(any('under your control) kills' in record.text for record in self.window.history_records))

    def test_hidden_dead_blackmagic_controller_stays_selectable_and_saved_plan_noops(self):
        self.check_hidden_blackmagic_plan('Dave', 'Hank')

    def test_hidden_dead_blackmagic_victim_stays_selectable_and_saved_plan_noops(self):
        self.check_hidden_blackmagic_plan('Hank', 'Dave')

    def test_available_blackmagic_plan_still_attacks_and_moves_controller(self):
        self.open_night(normal=False)
        self.window.selected_weapon = self.weapon('blackmagic')
        self.window.open_blackmagic_dialog()
        editor = self.window._weapon_windows['blackmagic']
        control_box, target_box = editor.findChildren(game.QComboBox)
        control_box.setCurrentIndex(control_box.findData(self.window.body_id('Dave')))
        target_box.setCurrentIndex(target_box.findData(self.window.body_id('Jerry')))
        next(button for button in editor.findChildren(game.QPushButton) if button.text() == 'Save').click()
        self.window.choose_room('Kitchen')
        self.window.draft_page.leave_button.click()
        attack, = self.window.night_attacks
        self.assertEqual((attack['attacker_label'], attack['target_label']), ('Dave', 'Jerry'))
        self.assertFalse(self.window.characters['Jerry']['alive'])
        self.assertIn('Dave', self.window.room_assignments['Attic'])
        self.assertNotIn('Dave', self.window.room_assignments['Kitchen'])

    def test_hidden_dead_knife_extra_target_saves_without_probe_and_adds_no_attack_or_bloodlust(self):
        self.hidden_dead_target_night('knife')
        self.window.open_knife_dialog()
        editor = self.window._weapon_windows['knife']
        box, = editor.findChildren(game.QComboBox)
        self.assertGreaterEqual(box.findData(self.window.body_id('Dave')), 0)
        box.setCurrentIndex(box.findData(self.window.body_id('Dave')))
        next(button for button in editor.findChildren(game.QPushButton) if button.text() == 'Confirm').click()
        self.assertTrue(editor.isHidden())
        self.assertEqual(self.window.knife_extra_target, 'Dave')
        self.window.choose_room('Kitchen')
        self.window.draft_page.leave_button.click()
        self.assertEqual([attack['target_label'] for attack in self.window.night_attacks], ['Hank'])
        self.assertFalse(self.window.knife_used_bloodlust)
        self.assertIsNone(self.window.pending_room_action)
        self.assertEqual(len(self.window.item_deaths), 1)
        self.assertFalse(any('covered in blood' in line.text for line in self.window.public_dialogue))

    def test_available_knife_extra_target_still_resolves_second_attack_and_bloodlust(self):
        self.open_night(normal=False)
        self.window.selected_weapon = self.weapon('knife')
        self.window.open_knife_dialog()
        editor = self.window._weapon_windows['knife']
        box, = editor.findChildren(game.QComboBox)
        box.setCurrentIndex(box.findData(self.window.body_id('Hank')))
        next(button for button in editor.findChildren(game.QPushButton) if button.text() == 'Confirm').click()
        self.window.choose_room('Attic')
        self.window.draft_page.leave_button.click()
        self.assertEqual(self.window.item_drafts.pending_offer.room_id, 'Kitchen')
        self.window.draft_page.leave_button.click()
        self.assertEqual([attack['target_label'] for attack in self.window.night_attacks], ['Jerry','Hank'])
        self.assertTrue(self.window.knife_used_bloodlust)

    def test_witnessed_death_is_removed_from_the_hard_night_roster(self):
        self.conversation()
        self.arm_optional('booby_trap', room='Attic')
        self.open_night(initial_count=1, normal=False, plan={
            'Hank':'Kitchen', 'Dave':'Attic', 'Jerry':'Library', 'Debra':'Basement', 'Owen':'Cellar'})
        self.assertIn('Dave', self.window.night_weapon_targets())
        self.window.loiter_button.click()
        self.assertFalse(self.window.characters['Dave']['alive'])
        self.assertNotIn('Dave', self.window.night_weapon_targets())
        self.window.selected_weapon = self.weapon('knife')
        self.window.open_knife_dialog()
        editor = self.window._weapon_windows['knife']
        box, = editor.findChildren(game.QComboBox)
        self.assertEqual(box.findData(self.window.body_id('Dave')), -1)

    def test_bonus_new_room_offers_first_reward_without_restarting_arrivals(self):
        state = self.open_night()
        with patch.object(self.window.npc_items, 'capture_night', wraps=self.window.npc_items.capture_night) as capture:
            self.assertTrue(self.window.prepare_room_entry('Kitchen'))
            cursor = state.cursor
            self.window.bonus_action_pending = True
            with patch.object(self.window, 'finish_room_choice') as finish:
                self.window.choose_room('Library')
                offer = self.window.item_drafts.pending_offer
                self.assertIsNotNone(offer)
                self.assertEqual(offer.room_id, 'Library')
                self.window.skip_item_offer(offer.offer_id)
                finish.assert_called_once_with('Library')
                self.window.choose_room('Library')
                self.assertIsNone(self.window.item_drafts.pending_offer)
                self.assertIsNone(self.window.draft_page)
                self.assertEqual(finish.call_count, 2)
            self.assertEqual(capture.call_count, 1)
        self.assertEqual(state.cursor, cursor)
        self.assertEqual(sum(map(len, self.window.room_assignments.values())), 5)


del ItemIntegrationTests
