"""Real Qt AI boundaries route identical appearances without rewriting dialogue."""
import random
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import test_conversation_integration as fixtures
from ai_context import SpeechProposal, render_character_context
from presentation_aliases import PresentationAliases


class LiveAppearanceRoutingTests(unittest.TestCase):
    setUpClass = fixtures.ConversationIntegrationTests.__dict__["setUpClass"]
    setUp = fixtures.ConversationIntegrationTests.setUp
    tearDown = fixtures.ConversationIntegrationTests.tearDown
    wait_for = fixtures.ConversationIntegrationTests.wait_for
    reply = staticmethod(fixtures.ConversationIntegrationTests.reply)
    target = fixtures.ConversationIntegrationTests.target
    send_direct = fixtures.ConversationIntegrationTests.send_direct
    free_floor = fixtures.ConversationIntegrationTests.free_floor
    prepare = fixtures.ConversationIntegrationTests.prepare
    cached = fixtures.ConversationIntegrationTests.cached

    def clones(self):
        window = self.window
        window.ensure_identity_state()
        for actor in ("Alex", "Jerry"):
            window.identity_state = window.identity_state.clone_appearance(window.body_id(actor), window.body_id("Dave"))
        window.invalidate_prepared_turn("test_clones", changed=True)
        window.presentation_aliases = PresentationAliases(random.Random(4))
        window.refresh_recipient_choices()
        return {handle: window.display_actor(handle) for handle in ("Alex", "Jerry", "Dave", "Hank")}

    def test_public_context_has_only_faces_references_and_this_npcs_private_original(self):
        labels = self.clones()
        self.assertEqual(len({labels[name] for name in ("Alex", "Jerry", "Dave")}), 3)
        state = self.window.public_state()
        self.assertEqual(set(state.alive), set(labels.values()))
        self.assertEqual({person.name for person in state.people_present}, {"Dave", "Hank"})
        context = self.window.build_character_context("Jerry")
        root = ET.fromstring(render_character_context(context))
        self.assertEqual(context.character, "Jerry")
        self.assertEqual(root.get("character"), labels["Jerry"])
        self.assertEqual(root.find("self").attrib,
                         {"name": "Jerry", "body": "Dave", "reference": labels["Jerry"]})
        self.assertNotIn("Alex", render_character_context(context))
        self.assertEqual(self.window.living_actor_handles(), ("Alex", "Dave", "Hank", "Jerry"))
        self.window.phase = "action"
        outside = self.window.public_state()
        self.assertFalse(outside.people_present)
        self.assertTrue(all("#" not in value for value in outside.alive))

    def test_direct_target_calls_exactly_that_body_and_routes_response_addressee(self):
        labels = self.clones()
        self.speech.side_effect = lambda context: SpeechProposal(labels["Alex"], "Dave was by the stairs.")
        self.window.run_conversation_loop = Mock()
        self.send_direct("Jerry")
        self.wait_for(lambda: not self.window.active_workers)
        self.assertEqual([call.args[0].character for call in self.speech.call_args_list], ["Jerry"])
        self.selector.assert_not_called()
        line = self.window.public_dialogue[-1]
        self.assertEqual((line.speaker, line.addressee, line.text),
                         ("Jerry", "Alex", "Dave was by the stairs."))
        perceived = self.window.recalled_dialogue()[-1]
        self.assertEqual((perceived.speaker, perceived.addressee), ("Dave", "Dave"))
        self.assertNotIn("#", perceived.text)

    def test_parallel_candidates_selector_keys_are_references_only_selected_body_is_published(self):
        labels = self.clones()
        self.speech.side_effect = lambda context: SpeechProposal(labels["Jerry"], "Dave heard footsteps.")
        self.selector.side_effect = lambda state, events, dialogue, proposals: labels["Dave"]
        self.window.run_conversation_loop = Mock()
        self.window.run_multiple_responses()
        self.wait_for(lambda: not self.window.active_workers)
        state, events, dialogue, proposals = self.selector.call_args.args
        self.assertEqual(set(proposals), {labels["Dave"], labels["Hank"], labels["Jerry"]})
        self.assertEqual({call.args[0].character for call in self.speech.call_args_list}, {"Dave", "Hank", "Jerry"})
        self.assertEqual(len(self.window.public_dialogue), 1)
        line = self.window.public_dialogue[-1]
        self.assertEqual((line.speaker, line.addressee, line.text), ("Dave", "Jerry", "Dave heard footsteps."))
        self.assertEqual(self.window.recalled_dialogue()[-1].addressee, "Dave")

    def test_prepared_selector_routes_only_selected_candidate_back_to_body_handles(self):
        labels = self.clones()
        self.free_floor()
        original_count = len(self.window.public_dialogue)
        self.speech.side_effect = lambda context: SpeechProposal(labels["Jerry"], "Dave heard footsteps.")
        self.selector.side_effect = lambda state, events, dialogue, proposals: labels["Dave"]
        self.prepare()
        self.cached()
        self.assertEqual(len(self.window.public_dialogue), original_count)
        self.assertEqual(set(self.selector.call_args.args[3]), {labels["Dave"], labels["Hank"], labels["Jerry"]})
        self.window.run_conversation_loop = Mock()
        self.assertTrue(self.window.consume_prepared_turn(self.window.group_turn_snapshot()))
        line = self.window.public_dialogue[-1]
        self.assertEqual((line.speaker, line.addressee, line.text), ("Dave", "Jerry", "Dave heard footsteps."))

    def test_vote_reference_selects_body_even_when_private_handle_names_another_face(self):
        labels = self.clones()
        with patch.object(self.window, "start_vote_requests"):
            self.window.run_voting_phase()
        with patch.object(self.window, "tally_votes"):
            self.window.collect_ai_vote("Hank", labels["Jerry"])
        self.assertEqual(self.window.votes["Hank"], "Jerry")
        self.assertEqual(self.window.body_id(self.window.votes["Hank"]), self.window.body_id("Jerry"))

    def test_vote_request_guard_rejects_new_identity_even_when_appearance_is_unchanged(self):
        self.clones()
        with patch.object(self.window, "start_vote_requests"):
            self.window.run_voting_phase()
        with patch.object(self.window, "start_ai_request") as start:
            self.window.start_vote_requests()
        guard = start.call_args.kwargs["context_guard"]
        self.assertTrue(guard())
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id("Alex"), self.window.body_id("Dave"))
        self.assertFalse(guard())

    def test_direct_completion_guard_rejects_same_appearance_identity_revision(self):
        self.clones()
        with patch.object(self.window, "start_ai_request") as start:
            self.window.run_single_response("Jerry")
        guard = start.call_args.kwargs["context_guard"]
        self.assertTrue(guard())
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id("Alex"), self.window.body_id("Dave"))
        self.assertFalse(guard())

    def test_first_speaker_metadata_uses_body_handle_while_detective_words_use_face(self):
        labels = self.clones()
        self.window.run_conversation_loop = Mock()
        with patch.object(self.window, "check_victory", return_value=False), patch.object(
                fixtures.game.random, "choice", return_value="Jerry"):
            self.window.run_conversation_phase()
        line = self.window.public_dialogue[-1]
        self.assertEqual(line.addressee, "Jerry")
        self.assertEqual(line.text, "Dave, let's start with you. Where were you last night?")
        self.assertNotIn("#", line.text)
        self.assertEqual(self.window.recalled_dialogue()[-1].addressee, "Dave")

    def test_snapshot_rejects_same_appearance_revision_and_inconsistent_body_mapping(self):
        self.clones()
        snapshot = self.window.group_turn_snapshot()
        self.assertTrue(self.window.ai_snapshot_is_current(snapshot))
        self.window.identity_state = self.window.identity_state.clone_appearance(
            self.window.body_id("Alex"), self.window.body_id("Dave"))
        self.assertFalse(self.window.ai_snapshot_is_current(snapshot))
        snapshot = self.window.group_turn_snapshot()
        self.window.body_ids["Hank"] += "unknown"
        self.assertFalse(self.window.ai_snapshot_is_current(snapshot))
