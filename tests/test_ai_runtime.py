"""Offline transport checks over real request schemas and XML payloads."""

import concurrent.futures
import json
from pathlib import Path
import sys
import tempfile
import traceback
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from xml.etree import ElementTree as ET

import httpx
from openai import APIConnectionError, APITimeoutError, AuthenticationError, BadRequestError, RateLimitError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ai_context import CharacterContext, DialogueLine, MemoryRecord, PublicState, SpeechProposal
from ai_runtime import AIConfig, AIRequestError, AIRuntime
from dev_trace import TraceRun


class AIRuntimeTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict("os.environ", {
            "ALIBAI_MODEL": "", "ALIBAI_SPEECH_MODEL": "",
            "ALIBAI_VOTE_MODEL": "", "ALIBAI_SELECTOR_MODEL": "",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.root.joinpath("prompts").mkdir()
        self.root.joinpath("System Prompt.txt").write_text("<character_policy>shared fixture</character_policy>", encoding="utf-8")
        for task in ("speech", "vote", "director"):
            self.root.joinpath("prompts", task + ".xml").write_text(f'<task name="{task}" />', encoding="utf-8")
        self.client = Mock()
        self.runtime = AIRuntime(client=self.client, prompt_root=self.root)
        self.state = PublicState(2, ("Jerry", "Dave", "Alex"), ("Debra",), ("Attic", "Library"))
        self.context = CharacterContext(
            "Jerry", ("private-persona-secret",), self.state,
            (MemoryRecord(1, "experience", "private-observation-secret", "self"),),
            (MemoryRecord(1, "disposition", "private-disposition-secret", "self"),),
            (MemoryRecord(1, "public_event", "A note was read.", "Detective"),),
            (DialogueLine(1, "Alex", "I was in the library."), DialogueLine(2, "Dave", "Jerry, answer me.")),
        )

    def response(self, payload, status="completed", output=()):
        self.client.responses.parse.return_value = SimpleNamespace(
            status=status, output=list(output), output_parsed=payload
        )

    def test_speech_and_vote_use_identical_evidence_with_distinct_tasks(self):
        self.response({"addressee": "Dave", "speech": "I was there."})
        self.assertEqual(self.runtime.speech(self.context), SpeechProposal("Dave", "I was there."))
        speech = self.client.responses.parse.call_args.kwargs
        self.response({"target": "Alex"})
        self.assertEqual(self.runtime.vote(self.context), "Alex")
        vote = self.client.responses.parse.call_args.kwargs
        self.assertEqual(speech["input"][1], vote["input"][1])
        self.assertEqual(ET.fromstring(speech["input"][0]["content"]).find("task").get("name"), "speech")
        self.assertEqual(ET.fromstring(vote["input"][0]["content"]).find("task").get("name"), "vote")
        for request in (speech, vote):
            self.assertIn("private-observation-secret", request["input"][1]["content"])
            self.assertIn('round="1"', request["input"][1]["content"])
            self.assertIn("I was in the library.", request["input"][1]["content"])
            self.assertFalse(request["store"])
            self.assertEqual(request["reasoning"], {"effort": "low"})
            self.assertEqual(request["timeout"], 45)
            self.assertEqual(request["model"], "gpt-6.1-terra")

    def test_speech_schema_excludes_self_and_dead_people_and_preserves_field_order(self):
        self.response({"addressee": None, "speech": "A reply."})
        self.runtime.speech(self.context)
        schema = self.client.responses.parse.call_args.kwargs["text_format"]
        self.assertEqual(list(schema.model_fields), ["addressee", "speech"])
        for target in ("Jerry", "Debra", "Unknown"):
            self.response({"addressee": target, "speech": "A reply."})
            with self.subTest(target=target), self.assertRaises(AIRequestError):
                self.runtime.speech(self.context)
        for target in (None, "Dave", "Alex"):
            self.response({"addressee": target, "speech": "A reply."})
            self.assertEqual(self.runtime.speech(self.context).addressee, target)

    def test_empty_whitespace_or_unexpected_fields_are_rejected(self):
        for payload in (
            {"addressee": None, "speech": ""},
            {"addressee": None, "speech": "   "},
            {"addressee": None, "speech": "Text", "private_analysis": "hidden"},
            {"speech": "Missing recipient field"},
        ):
            self.response(payload)
            with self.subTest(payload=payload), self.assertRaises(AIRequestError):
                self.runtime.speech(self.context)

    def test_vote_schema_accepts_only_living_other_suspects_or_abstention(self):
        for target in ("Alex", "Dave", "NONE"):
            self.response({"target": target})
            self.assertEqual(self.runtime.vote(self.context), target)
        for target in ("Jerry", "Debra", "Unknown", "", None):
            self.response({"target": target})
            with self.subTest(target=target), self.assertRaises(AIRequestError):
                self.runtime.vote(self.context)

    def test_selector_gets_public_context_and_drafts_without_private_character_data(self):
        proposals = {
            "Jerry": SpeechProposal("Alex", "I saw Alex leave."),
            "Dave": SpeechProposal(None, "Let me speak."),
        }
        self.response({"speaker": "Dave"})
        selected = self.runtime.select_reply(
            self.state, self.context.public_events, self.context.dialogue, proposals
        )
        self.assertEqual(selected, "Dave")
        request = self.client.responses.parse.call_args.kwargs
        self.assertEqual(ET.fromstring(request["input"][0]["content"]).get("name"), "director")
        payload = request["input"][1]["content"]
        self.assertIn("I saw Alex leave.", payload)
        self.assertIn("Let me speak.", payload)
        self.assertIn("A note was read.", payload)
        self.assertIn("I was in the library.", payload)
        for private in ("private-persona-secret", "private-observation-secret", "private-disposition-secret", "character_policy"):
            self.assertNotIn(private, payload)
            self.assertNotIn(private, request["input"][0]["content"])
        self.assertEqual(len(self.client.responses.parse.call_args_list), 1)
        self.assertEqual(self.context.dialogue[-1].text, "Jerry, answer me.")

    def test_selector_cannot_select_unproposed_speaker_or_rewrite_reply(self):
        proposals = {"Jerry": SpeechProposal(None, "Original words.")}
        for payload in ({"speaker": "Dave"}, {"speaker": "Jerry", "speech": "Replacement"}):
            self.response(payload)
            with self.subTest(payload=payload), self.assertRaises(AIRequestError):
                self.runtime.select_reply(self.state, (), (), proposals)
        self.response({"speaker": "Jerry"})
        self.assertEqual(self.runtime.select_reply(self.state, (), (), proposals), "Jerry")
        self.assertEqual(proposals["Jerry"].speech, "Original words.")

    def test_selector_rejects_empty_dead_or_blank_proposals_before_request(self):
        for proposals in ({}, {"Debra": SpeechProposal(None, "Text")}, {"Jerry": SpeechProposal(None, " ")}):
            with self.subTest(proposals=proposals), self.assertRaises(AIRequestError):
                self.runtime.select_reply(self.state, (), (), proposals)
        self.client.responses.parse.assert_not_called()

    def test_incomplete_refused_or_unparsed_response_raises_without_fabricating_result(self):
        for status in ("incomplete", "failed", "cancelled", "in_progress", None):
            self.response({"target": "NONE"}, status=status)
            with self.subTest(status=status), self.assertRaises(AIRequestError):
                self.runtime.vote(self.context)
        self.response(None)
        with self.assertRaises(AIRequestError):
            self.runtime.vote(self.context)
        refusal = SimpleNamespace(content=[SimpleNamespace(type="refusal", refusal="refused")])
        self.response({"target": "NONE"}, output=[refusal])
        with self.assertRaises(AIRequestError):
            self.runtime.vote(self.context)

    def test_api_errors_have_actionable_messages_without_remote_details(self):
        request = httpx.Request("POST", "https://api.openai.com/v1/responses")
        cases = (
            (AuthenticationError("remote-secret", response=httpx.Response(401, request=request), body={}), "key was rejected"),
            (RateLimitError("remote-secret", response=httpx.Response(429, request=request), body={}), "usage limit"),
            (APITimeoutError(request=request), "timed out"),
            (APIConnectionError(message="remote-secret", request=request), "connect"),
            (BadRequestError("remote-secret", response=httpx.Response(400, request=request), body={"code": "context_length_exceeded"}), "context limit"),
        )
        for error, expected in cases:
            self.client.responses.parse.side_effect = error
            with self.subTest(error=type(error).__name__), self.assertRaises(AIRequestError) as raised:
                self.runtime.vote(self.context)
            self.assertIn(expected, str(raised.exception))
            self.assertNotIn("remote-secret", str(raised.exception))
            self.assertNotIn("remote-secret", "".join(traceback.format_exception(raised.exception)))

    def test_client_is_lazy_and_created_once_for_parallel_requests(self):
        runtime = AIRuntime(prompt_root=self.root)
        fake_client = Mock()
        fake_client.responses.parse.return_value = SimpleNamespace(status="completed", output=[], output_parsed={"target": "NONE"})
        with patch("ai_runtime.OpenAI", return_value=fake_client) as constructor:
            constructor.assert_not_called()
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                results = list(executor.map(runtime.vote, [self.context] * 5))
            self.assertEqual(results, ["NONE"] * 5)
            constructor.assert_called_once_with(timeout=45.0, max_retries=1)

    def test_model_configuration_has_role_overrides_and_common_fallback(self):
        with patch.dict("os.environ", {"ALIBAI_MODEL": "common", "ALIBAI_SPEECH_MODEL": "speech", "ALIBAI_VOTE_MODEL": "vote", "ALIBAI_SELECTOR_MODEL": "selector"}):
            config = AIConfig()
        self.assertEqual((config.speech_model, config.vote_model, config.selector_model), ("speech", "vote", "selector"))
        with patch.dict("os.environ", {"ALIBAI_MODEL": "common", "ALIBAI_SPEECH_MODEL": "", "ALIBAI_VOTE_MODEL": "", "ALIBAI_SELECTOR_MODEL": ""}):
            config = AIConfig()
        self.assertEqual((config.speech_model, config.vote_model, config.selector_model), ("common",) * 3)
        for limits in ({"max_retries": 5}, {"timeout_seconds": 0}, {"reasoning_effort": "minimal"}):
            with self.subTest(limits=limits), self.assertRaises(ValueError):
                AIConfig(**limits)

    def test_enabled_trace_captures_actual_input_schema_and_only_allowed_response_fields(self):
        trace = TraceRun(enabled=True, root=self.root / "traces")
        trace.start({})
        self.runtime.trace = trace
        self.client.api_key = "credential-sentinel"
        self.client.default_headers = {"Authorization": "credential-sentinel"}
        output = [
            SimpleNamespace(type="reasoning", encrypted_content="provider-internal-sentinel"),
            SimpleNamespace(type="message", content=[SimpleNamespace(type="output_text", text='{"target":"Alex"}')]),
        ]
        self.response({"target": "Alex"}, output=output)
        self.runtime.vote(self.context)
        text = trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8")
        records = [json.loads(line) for line in text.splitlines()]
        start = next(record for record in records if record["kind"] == "request_started")
        actual = self.client.responses.parse.call_args.kwargs
        self.assertEqual(start["payload"]["input"], actual["input"])
        self.assertEqual(start["payload"]["output_schema"], actual["text_format"].model_json_schema())
        self.assertEqual(start["payload"]["actor"], "Jerry")
        self.assertEqual(start["payload"]["task"], "vote")
        response = next(record for record in records if record["kind"] == "request_response")
        complete = next(record for record in records if record["kind"] == "request_completed")
        self.assertEqual(response["request_id"], start["request_id"])
        self.assertEqual(complete["request_id"], start["request_id"])
        self.assertEqual(response["payload"]["messages"], [{"type": "output_text", "text": '{"target":"Alex"}'}])
        self.assertEqual(complete["payload"]["result"], {"target": "Alex"})
        self.assertNotIn("credential-sentinel", text)
        self.assertNotIn("provider-internal-sentinel", text)

    def test_enabled_trace_records_failed_auth_category_without_remote_error_details(self):
        trace = TraceRun(enabled=True, root=self.root / "traces")
        trace.start({})
        self.runtime.trace = trace
        request = httpx.Request("POST", "https://example.invalid", headers={"Authorization": "credential-sentinel"})
        self.client.responses.parse.side_effect = AuthenticationError(
            "credential-sentinel", response=httpx.Response(401, request=request), body={"detail": "credential-sentinel"}
        )
        with self.assertRaises(AIRequestError):
            self.runtime.vote(self.context)
        text = trace.run_dir.joinpath("events.jsonl").read_text(encoding="utf-8")
        records = [json.loads(line) for line in text.splitlines()]
        failed = next(record for record in records if record["kind"] == "request_failed")
        self.assertEqual(failed["payload"]["error_type"], "AuthenticationError")
        self.assertFalse(any(record["kind"] == "request_completed" for record in records))
        self.assertNotIn("credential-sentinel", text)

    def test_missing_or_malformed_prompt_fails_before_client_access(self):
        self.root.joinpath("prompts/vote.xml").write_text("<broken", encoding="utf-8")
        with self.assertRaises(AIRequestError):
            self.runtime.vote(self.context)
        self.root.joinpath("prompts/vote.xml").unlink()
        with self.assertRaises(AIRequestError):
            self.runtime.vote(self.context)
        self.client.responses.parse.assert_not_called()


if __name__ == "__main__":
    unittest.main()
