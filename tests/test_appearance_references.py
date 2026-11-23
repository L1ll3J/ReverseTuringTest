"""Appearance-only public references and stable within-phase duplicate choices."""
from dataclasses import replace
import random
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from ai_context import (APPEARANCE_LABELS, CharacterContext, DialogueLine, MemoryRecord,
                        PersonPresent, PublicState, SelfIdentity, SpeechProposal,
                        render_character_context, render_selector_context)
from ai_runtime import AIRuntime, AIRequestError
from identity_state import IdentityState
from presentation_aliases import PresentationAliases


class AppearanceReferenceTests(unittest.TestCase):
    def setUp(self):
        self.state = PublicState(2, ("Dave #1", "Dave #2", "Alex"), ("Hank",), ("Attic",),
                                 (PersonPresent("Dave", "Dave #1"), PersonPresent("Dave", "Dave #2"),
                                  PersonPresent("Alex", "Alex")))
        self.memory = MemoryRecord(1, "experience", "You saw Dave by the stairs.", "self")
        self.context = CharacterContext("private-body-route", (), self.state, (self.memory,), (), (),
                                        (DialogueLine(1, "Dave", "Dave was upstairs.", "Alex"),),
                                        self_identity=SelfIdentity("Jerry", "Dave", "Dave #2"))
        self.client = Mock()
        self.runtime = AIRuntime(client=self.client)
        self.runtime._character_instructions = Mock(return_value="<instructions />")
        self.runtime._load_prompt = Mock(return_value="<selector />")

    def response(self, **payload):
        self.client.responses.parse.return_value = SimpleNamespace(
            status="completed", output=[], output_parsed=payload)

    def test_public_rows_and_approved_guidance_are_shared_without_private_self(self):
        character = ET.fromstring(render_character_context(self.context))
        selector = ET.fromstring(render_selector_context(self.state, (), (), {}))
        approved = ET.fromstring(APPEARANCE_LABELS)
        for root in (character, selector):
            self.assertEqual(root.find("appearance_labels").text, approved.text)
            self.assertEqual([node.attrib for node in root.findall("people_present/person")],
                             [{"name": "Dave", "reference": "Dave #1"},
                              {"name": "Dave", "reference": "Dave #2"},
                              {"name": "Alex", "reference": "Alex"}])
            self.assertNotIn("private-body-route", ET.tostring(root, encoding="unicode"))
        self.assertIsNone(selector.find("self"))
        self.assertNotIn("Jerry", ET.tostring(selector, encoding="unicode"))
        self.assertEqual(character.get("character"), "Dave #2")
        self.assertEqual(character.find("private_experiences").get("recipient"), "Dave #2")
        self.assertEqual(character.find("private_experiences/record").text, self.memory.text)
        self.assertEqual(character.find("public_dialogue/utterance").attrib,
                         {"round": "1", "speaker": "Dave", "addressee": "Alex"})

    def test_speech_vote_and_selector_use_references_without_rewriting_words(self):
        self.response(addressee="Dave #1", speech="Dave waited by the stairs.")
        proposal = self.runtime.speech(self.context)
        self.assertEqual(proposal, SpeechProposal("Dave #1", "Dave waited by the stairs."))
        self.assertEqual(self.runtime._eligible_targets(self.context), ("Dave #1", "Alex"))
        self.response(target="Dave #1")
        self.assertEqual(self.runtime.vote(self.context), "Dave #1")
        self.response(speaker="Dave #2")
        selected = self.runtime.select_reply(self.state, (), (), {"Dave #2": proposal})
        self.assertEqual(selected, "Dave #2")
        request = self.client.responses.parse.call_args.kwargs
        selector = ET.fromstring(request["input"][1]["content"])
        self.assertEqual(selector.find("proposals/proposal").attrib,
                         {"speaker": "Dave #2", "addressee": "Dave #1"})
        self.assertEqual(selector.find("proposals/proposal").text, proposal.speech)
        self.assertEqual(self.memory.text, "You saw Dave by the stairs.")

    def test_internal_route_and_own_reference_cannot_be_selected_as_targets(self):
        for value in ("private-body-route", "Dave", "Dave #2"):
            with self.subTest(target=value):
                self.response(target=value)
                with self.assertRaises(AIRequestError):
                    self.runtime.vote(self.context)
                self.response(addressee=value, speech="A reply.")
                with self.assertRaises(AIRequestError):
                    self.runtime.speech(self.context)
        self.response(speaker="private-body-route")
        with self.assertRaises(AIRequestError):
            self.runtime.select_reply(self.state, (), (), {"Dave #2": SpeechProposal(None, "A reply.")})

    def test_muffled_response_bank_uses_only_current_reference(self):
        self.response(tone=0)
        self.runtime.muffled_tone(self.context)
        request = self.client.responses.parse.call_args.kwargs
        root = ET.fromstring(request["input"][1]["content"])
        self.assertEqual(root.find("muffled_responses").get("recipient"), "Dave #2")
        self.assertNotIn("private-body-route", request["input"][1]["content"])

    def test_defaults_remain_compatible_and_typed_rows_reject_ambiguous_mapping(self):
        legacy = replace(self.state, alive=("Dave", "Alex"), people_present=())
        xml = render_selector_context(legacy, (), (), {})
        self.assertNotIn("appearance_labels", xml)
        self.assertNotIn("people_present", xml)
        for rows in ((PersonPresent("Dave", "Dave #1"),),
                     (PersonPresent("Dave", "Dave #1"), PersonPresent("Dave", "Dave #1")),
                     ("raw-row",)):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                replace(self.state, people_present=rows)
        rows = list(self.state.people_present)
        snapshot = replace(self.state, people_present=rows)
        rows.clear()
        self.assertEqual(snapshot.people_present, self.state.people_present)


class StableAliasTests(unittest.TestCase):
    def setUp(self):
        self.identity = IdentityState.from_bodies("player", "a", ("b", "c"),
                                                  appearance_names={"a": "Dave", "b": "Dave", "c": "Dave"})

    def test_three_to_two_survivors_keep_assigned_labels_for_discussion_and_vote(self):
        aliases = PresentationAliases(random.Random(4))
        full = aliases.for_presence("discussion:1", self.identity, ("a", "b", "c"))
        for removed in ("a", "b", "c"):
            remaining = tuple(body for body in ("a", "b", "c") if body != removed)
            reduced = aliases.for_presence("discussion:1", self.identity, remaining)
            for body in remaining:
                self.assertEqual(reduced.label_for_body(body), full.label_for_body(body))
        singleton = aliases.for_presence("discussion:1", self.identity, ("b",))
        self.assertEqual(singleton.label_for_body("b"), "Dave")
        self.assertEqual(aliases.for_presence("discussion:1", self.identity, ("a", "b", "c")), full)

    def test_growing_duplicate_group_preserves_assignments_and_does_not_reuse_departed_label(self):
        aliases = PresentationAliases(random.Random(3))
        pair = aliases.for_presence("phase", self.identity, ("a", "b"))
        grown = aliases.for_presence("phase", self.identity, ("a", "b", "c"))
        for body in ("a", "b"):
            self.assertEqual(pair.label_for_body(body), grown.label_for_body(body))
        self.assertEqual(len(set(label for _, label in grown.entries)), 3)
        self.assertEqual(self.identity.appearances, (("a", "Dave"), ("b", "Dave"), ("c", "Dave")))

    def test_player_number_does_not_reveal_the_original(self):
        numbers = {PresentationAliases(random.Random(seed)).for_presence("phase", self.identity,
                   ("a", "b", "c")).label_for_body("a") for seed in range(10)}
        self.assertGreater(len(numbers), 1)
