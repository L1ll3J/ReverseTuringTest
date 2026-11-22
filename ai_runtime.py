"""Structured OpenAI requests over immutable, character-scoped game context."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
from threading import Lock
from time import monotonic_ns
from uuid import uuid4
from typing import Annotated, Literal, Mapping
from xml.etree import ElementTree

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    OpenAIError,
    PermissionDeniedError,
    RateLimitError,
)
from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError, create_model

from ai_context import (
    CharacterContext,
    DialogueLine,
    MemoryRecord,
    PublicState,
    SpeechProposal,
    render_character_context,
    render_selector_context,
)


class AIRequestError(Exception):
    """A request failed without changing the game's dialogue or ballots."""


def _role_model(role):
    return (os.getenv(f"ALIBAI_{role}_MODEL") or os.getenv("ALIBAI_MODEL") or "gpt-6.1-terra").strip()


@dataclass(frozen=True)
class AIConfig:
    speech_model: str = field(default_factory=lambda: _role_model("SPEECH"))
    vote_model: str = field(default_factory=lambda: _role_model("VOTE"))
    selector_model: str = field(default_factory=lambda: _role_model("SELECTOR"))
    reasoning_effort: str = "low"
    timeout_seconds: float = 45.0
    max_retries: int = 1
    max_output_tokens: int = 2048

    def __post_init__(self):
        if not all(model.strip() for model in (self.speech_model, self.vote_model, self.selector_model)):
            raise ValueError("Each AI role requires a model name")
        if self.reasoning_effort not in {"low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Unsupported reasoning effort")
        if self.timeout_seconds <= 0 or not 0 <= self.max_retries <= 2 or self.max_output_tokens < 1:
            raise ValueError("AI request limits must be positive and retries bounded")


def _literal(values):
    return Literal.__getitem__(tuple(values))


def _schema(name, **fields):
    return create_model(name, __config__=ConfigDict(extra="forbid", strict=True), **fields)


class AIRuntime:
    def __init__(self, config=None, client=None, prompt_root=None, trace=None):
        self.config = config if config is not None else AIConfig()
        self.trace = trace
        self._client = client
        self._client_lock = Lock()
        default_root = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
        self.prompt_root = Path(prompt_root if prompt_root is not None else default_root)

    def speech(self, context: CharacterContext, *, trace_scope=None) -> SpeechProposal:
        eligible = self._eligible_targets(context)
        addressee_type = _literal(eligible) | None if eligible else type(None)
        text_type = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
        output_schema = _schema(
            "CharacterSpeech",
            addressee=(addressee_type, ...),
            speech=(text_type, ...),
        )
        result = self._request(
            self.config.speech_model,
            self._character_instructions("speech"),
            render_character_context(context),
            output_schema,
            trace_scope=trace_scope, task="speech", actor=context.character,
        )
        return SpeechProposal(addressee=result.addressee, speech=result.speech)

    def vote(self, context: CharacterContext, *, trace_scope=None) -> str:
        eligible = self._eligible_targets(context)
        output_schema = _schema("CharacterVote", target=(_literal((*eligible, "NONE")), ...))
        result = self._request(
            self.config.vote_model,
            self._character_instructions("vote"),
            render_character_context(context),
            output_schema,
            trace_scope=trace_scope, task="vote", actor=context.character,
        )
        return result.target

    def select_reply(
        self,
        state: PublicState,
        public_events: tuple[MemoryRecord, ...],
        dialogue: tuple[DialogueLine, ...],
        proposals: Mapping[str, SpeechProposal],
        *, trace_scope=None,
    ) -> str:
        proposals = dict(proposals)
        if not proposals or any(speaker not in state.alive for speaker in proposals):
            raise AIRequestError("There are no valid replies to select. Please retry.")
        if any(not proposal.speech.strip() for proposal in proposals.values()):
            raise AIRequestError("A proposed reply was empty. Please retry.")
        output_schema = _schema("ReplySelection", speaker=(_literal(tuple(proposals)), ...))
        result = self._request(
            self.config.selector_model,
            self._load_prompt("prompts/director.xml"),
            render_selector_context(state, public_events, dialogue, proposals),
            output_schema,
            trace_scope=trace_scope, task="selector", actor=None,
        )
        return result.speaker

    @staticmethod
    def _eligible_targets(context):
        if context.character not in context.state.alive:
            raise AIRequestError("This character is no longer available to respond.")
        return tuple(dict.fromkeys(name for name in context.state.alive if name != context.character))

    def _load_prompt(self, relative_path):
        try:
            text = self.prompt_root.joinpath(relative_path).read_text(encoding="utf-8").strip()
            ElementTree.fromstring(text)
            return text
        except (OSError, UnicodeError, ElementTree.ParseError):
            raise AIRequestError("Required game instructions are missing or unreadable. Please check the installation.") from None

    def _character_instructions(self, task):
        shared = self._load_prompt("System Prompt.txt")
        specific = self._load_prompt(f"prompts/{task}.xml")
        return f"<instructions>\n{shared}\n{specific}\n</instructions>"

    def _get_client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = OpenAI(
                        timeout=self.config.timeout_seconds,
                        max_retries=self.config.max_retries,
                    )
        return self._client

    @staticmethod
    def _response_trace(response):
        messages = []
        for item in response.output or ():
            if getattr(item, "type", "message") != "message":
                continue
            for content in getattr(item, "content", ()) or ():
                kind = getattr(content, "type", None)
                if kind == "output_text":
                    messages.append({"type": kind, "text": content.text})
                elif kind == "refusal":
                    messages.append({"type": kind, "text": content.refusal})
        usage = getattr(response, "usage", None)
        counts = {}
        for name in ("input_tokens", "output_tokens", "total_tokens"):
            value = getattr(usage, name, None)
            if isinstance(value, int):
                counts[name] = value
        for details, name in (("input_tokens_details", "cached_tokens"),
                              ("output_tokens_details", "reasoning_tokens")):
            value = getattr(getattr(usage, details, None), name, None)
            if isinstance(value, int):
                counts[name] = value
        return {"provider_response_id": getattr(response, "id", None),
                "status": getattr(response, "status", None),
                "messages": messages, "usage": counts}

    def _request(self, model, instructions, context_xml, output_schema,
                 *, trace_scope=None, task="request", actor=None):
        scope = trace_scope
        if scope is None and self.trace is not None:
            scope = self.trace.new_job(task)
        request_id = uuid4().hex
        started = monotonic_ns()
        if scope is not None:
            scope.record("request_started", {
                "task": task, "actor": actor, "model": model,
                "input": [{"role": "system", "content": instructions},
                          {"role": "user", "content": context_xml}],
                "output_schema": output_schema.model_json_schema(),
                "reasoning": {"effort": self.config.reasoning_effort},
                "max_output_tokens": self.config.max_output_tokens,
                "timeout_seconds": self.config.timeout_seconds,
                "sdk_max_retries": self.config.max_retries, "store": False,
            }, request_id=request_id)

        def response_received(response):
            if scope is not None:
                try:
                    payload = self._response_trace(response)
                except Exception:
                    payload = {"metadata_available": False}
                scope.record("request_response", payload, request_id=request_id)

        try:
            result = self._perform_request(model, instructions, context_xml, output_schema,
                                           on_response=response_received)
        except AIRequestError as error:
            if scope is not None:
                cause = error.__context__
                scope.record("request_failed", {
                    "task": task, "actor": actor,
                    "error_type": type(cause).__name__ if cause is not None else type(error).__name__,
                    "duration_ns": monotonic_ns() - started,
                }, request_id=request_id)
            raise
        except Exception:
            if scope is not None:
                scope.record("request_failed", {"task": task, "actor": actor,
                             "error_type": "UnexpectedError", "duration_ns": monotonic_ns() - started},
                             request_id=request_id)
            raise
        if scope is not None:
            scope.record("request_completed", {"task": task, "actor": actor,
                         "result": result.model_dump(), "duration_ns": monotonic_ns() - started},
                         request_id=request_id)
        return result

    def _perform_request(self, model, instructions, context_xml, output_schema, *, on_response):
        try:
            response = self._get_client().responses.parse(
                model=model,
                input=[
                    {"role": "system", "content": instructions},
                    {"role": "user", "content": context_xml},
                ],
                text_format=output_schema,
                reasoning={"effort": self.config.reasoning_effort},
                max_output_tokens=self.config.max_output_tokens,
                timeout=self.config.timeout_seconds,
                store=False,
            )
            on_response(response)
            if response.status != "completed":
                raise AIRequestError("The AI response did not complete. Please retry.")
            for item in response.output or ():
                if any(getattr(content, "type", None) == "refusal" for content in getattr(item, "content", ()) or ()):
                    raise AIRequestError("The AI could not provide that response. Please retry or continue differently.")
            if response.output_parsed is None:
                raise AIRequestError("The AI returned no usable response. Please retry.")
            payload = response.output_parsed
            if isinstance(payload, BaseModel):
                payload = payload.model_dump()
            return output_schema.model_validate(payload)
        except AuthenticationError:
            raise AIRequestError("The API key was rejected. Check your OpenAI credentials before retrying.") from None
        except PermissionDeniedError:
            raise AIRequestError("The API key cannot access this model. Check model access before retrying.") from None
        except RateLimitError:
            raise AIRequestError("OpenAI's usage limit was reached. Check your quota or wait before retrying.") from None
        except APITimeoutError:
            raise AIRequestError("The AI request timed out. Please retry.") from None
        except APIConnectionError:
            raise AIRequestError("Could not connect to OpenAI. Check your connection and retry.") from None
        except BadRequestError as error:
            if error.code == "context_length_exceeded":
                message = "This conversation exceeds the selected model's context limit. No dialogue was removed."
            else:
                message = "The AI request configuration was rejected. Check model settings before retrying."
            raise AIRequestError(message) from None
        except NotFoundError:
            raise AIRequestError("The selected AI model is unavailable. Check model settings before retrying.") from None
        except APIStatusError:
            raise AIRequestError("OpenAI could not complete the request. Please retry.") from None
        except OpenAIError:
            raise AIRequestError("The AI client could not complete the request. Check your credentials and settings.") from None
        except (ValidationError, TypeError, ValueError, AttributeError):
            raise AIRequestError("The AI returned an invalid response. Please retry.") from None
