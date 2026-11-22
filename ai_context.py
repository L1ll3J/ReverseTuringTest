"""Immutable, source-separated inputs for character and reply-selection requests."""

from dataclasses import dataclass
from typing import Mapping
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class MemoryRecord:
    round_number: int
    kind: str
    text: str
    source: str = ""


@dataclass(frozen=True)
class DialogueLine:
    round_number: int
    speaker: str
    text: str


@dataclass(frozen=True)
class PublicState:
    round_number: int
    alive: tuple[str, ...]
    dead: tuple[str, ...]
    available_rooms: tuple[str, ...]

    def __post_init__(self):
        for name in ("alive", "dead", "available_rooms"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class CharacterContext:
    character: str
    persona: tuple[str, ...]
    state: PublicState
    experiences: tuple[MemoryRecord, ...]
    dispositions: tuple[MemoryRecord, ...]
    public_events: tuple[MemoryRecord, ...]
    dialogue: tuple[DialogueLine, ...]

    def __post_init__(self):
        for name in ("persona", "experiences", "dispositions", "public_events", "dialogue"):
            object.__setattr__(self, name, tuple(getattr(self, name)))


@dataclass(frozen=True)
class SpeechProposal:
    addressee: str | None
    speech: str


def _append_public_state(parent: ET.Element, state: PublicState) -> None:
    element = ET.SubElement(parent, "public_state", {"round": str(state.round_number)})
    for tag, names in (("alive", state.alive), ("dead", state.dead)):
        group = ET.SubElement(element, tag)
        for name in names:
            ET.SubElement(group, "character", {"name": name})
    rooms = ET.SubElement(element, "available_rooms")
    for name in state.available_rooms:
        ET.SubElement(rooms, "room", {"name": name})


def _append_records(
    parent: ET.Element,
    tag: str,
    records: tuple[MemoryRecord, ...],
    kind: str,
    recipient: str | None = None,
) -> None:
    attributes = {"recipient": recipient} if recipient is not None else {}
    section = ET.SubElement(parent, tag, attributes)
    for record in records:
        if record.kind != kind:
            raise ValueError(f"{tag} requires {kind} records")
        attributes = {"round": str(record.round_number), "kind": record.kind}
        if record.source:
            attributes["source"] = record.source
        ET.SubElement(section, "record", attributes).text = record.text


def _append_dialogue(parent: ET.Element, dialogue: tuple[DialogueLine, ...]) -> None:
    section = ET.SubElement(parent, "public_dialogue")
    for line in dialogue:
        ET.SubElement(
            section,
            "utterance",
            {"round": str(line.round_number), "speaker": line.speaker},
        ).text = line.text


def render_character_context(context: CharacterContext) -> str:
    root = ET.Element(
        "character_context",
        {"character": context.character, "round": str(context.state.round_number)},
    )
    persona = ET.SubElement(root, "persona")
    for trait in context.persona:
        ET.SubElement(persona, "trait").text = trait
    _append_public_state(root, context.state)
    _append_records(
        root, "private_experiences", context.experiences, "experience", context.character
    )
    _append_records(
        root, "dispositions", context.dispositions, "disposition", context.character
    )
    _append_records(root, "public_events", context.public_events, "public_event")
    _append_dialogue(root, context.dialogue)
    return ET.tostring(root, encoding="unicode")


def render_selector_context(
    state: PublicState,
    public_events: tuple[MemoryRecord, ...],
    dialogue: tuple[DialogueLine, ...],
    proposals: Mapping[str, SpeechProposal],
) -> str:
    root = ET.Element("reply_selection_context", {"round": str(state.round_number)})
    _append_public_state(root, state)
    _append_records(root, "public_events", tuple(public_events), "public_event")
    _append_dialogue(root, tuple(dialogue))
    section = ET.SubElement(root, "proposals")
    for speaker, proposal in tuple(proposals.items()):
        attributes = {"speaker": speaker}
        if proposal.addressee is not None:
            attributes["addressee"] = proposal.addressee
        ET.SubElement(section, "proposal", attributes).text = proposal.speech
    return ET.tostring(root, encoding="unicode")
