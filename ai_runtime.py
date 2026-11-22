"""Structured OpenAI requests over immutable, character-scoped game context."""

from dataclasses import dataclass, field
import os
from pathlib import Path
import sys
from threading import Lock
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
    def __init__(self, config=None, client=None, prompt_root=None):
        self.config = config if config is not None else AIConfig()
        self._client = client
        self._client_lock = Lock()
        default_root = getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)
        self.prompt_root = Path(prompt_root if prompt_root is not None else default_root)

    def speech(self, context: CharacterContext) -> SpeechProposal:
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
        )
        return SpeechProposal(addressee=result.addressee, speech=result.speech)

    def vote(self, context: CharacterContext) -> str:
        eligible = self._eligible_targets(context)
        output_schema = _schema("CharacterVote", target=(_literal((*eligible, "NONE")), ...))
        result = self._request(
            self.config.vote_model,
            self._character_instructions("vote"),
            render_character_context(context),
            output_schema,
        )
        return result.target

    def select_reply(
        self,
        state: PublicState,
        public_events: tuple[MemoryRecord, ...],
        dialogue: tuple[DialogueLine, ...],
        proposals: Mapping[str, SpeechProposal],
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

    def _request(self, model, instructions, context_xml, output_schema):
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
