"""Approved private self metadata preserves body routing in every character task."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from xml.etree import ElementTree as ET

from ai_context import CharacterContext, PublicState, SelfIdentity
from ai_runtime import AIRuntime


class SoulRuntimeTests(unittest.TestCase):
    def test_original_self_name_is_private_metadata_and_never_the_actor_route(self):
        context = CharacterContext("Jerry", (), PublicState(1, ("Jerry", "Dave", "Alex"), (), ()),
                                   (), (), (), (), self_identity=SelfIdentity("Dave", "Jerry", "Jerry #2"))
        runtime = AIRuntime()
        runtime._character_instructions = Mock(return_value="<instructions />")
        for task, response in (("speech", SimpleNamespace(addressee="Dave", speech="A reply.")),
                               ("vote", SimpleNamespace(target="Dave")),
                               ("muffled_tone", SimpleNamespace(tone=0))):
            with self.subTest(task=task):
                runtime._request = Mock(return_value=response)
                getattr(runtime, task)(context)
                args, kwargs = runtime._request.call_args
                root = ET.fromstring(args[2])
                self.assertEqual(root.find("self").attrib,
                                 {"name": "Dave", "body": "Jerry", "reference": "Jerry #2"})
                self.assertEqual(root.get("character"), "Jerry")
                self.assertEqual(kwargs["actor"], "Jerry")
                self.assertEqual(runtime._eligible_targets(context), ("Dave", "Alex"))
                self.assertEqual([node.get("name") for node in root.findall("public_state/alive/character")],
                                 ["Jerry", "Dave", "Alex"])
                self.assertEqual(root.findall(".//original_identity"), [])
