"""Immutable, source-separated inputs for character and reply-selection requests."""

from dataclasses import dataclass
from enum import Enum
from typing import Mapping
from xml.etree import ElementTree as ET


@dataclass(frozen=True)
class MemoryRecord:
    round_number: int
    kind: str
    text: str
    source: str = ""


class FeelingKind(str, Enum):
    INFATUATION = "infatuation"
    BITTERNESS = "bitterness"


@dataclass(frozen=True)
class CurrentFeeling:
    kind: FeelingKind
    giver: str

    def __post_init__(self):
        if not isinstance(self.kind, FeelingKind):
            raise ValueError("A current feeling requires a known kind")
        if not isinstance(self.giver, str) or not self.giver.strip():
            raise ValueError("A current feeling requires its original subject name")


@dataclass(frozen=True)
class DialogueLine:
    round_number: int
    speaker: str
    text: str
    addressee: str | None = None


APPEARANCE_LABELS = """<appearance_labels>
  Several people may share an identical appearance. References distinguish
  the people currently present for addressing and voting. They are not part
  of anyone's name, and do not reveal who is the original.
  Speak using people's names and what you actually perceived.
</appearance_labels>"""


@dataclass(frozen=True)
class PersonPresent:
    name: str
    reference: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip() for value in (self.name, self.reference)):
            raise ValueError("A present person requires an appearance name and current reference")


@dataclass(frozen=True)
class PublicState:
    round_number: int
    alive: tuple[str, ...]
    dead: tuple[str, ...]
    available_rooms: tuple[str, ...]
    people_present: tuple[PersonPresent, ...] = ()

    def __post_init__(self):
        for name in ("alive", "dead", "available_rooms", "people_present"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if any(not isinstance(person, PersonPresent) for person in self.people_present):
            raise ValueError("Present people must be typed snapshots")
        references = tuple(person.reference for person in self.people_present)
        if references and (len(set(references)) != len(references)
                           or len(self.alive) != len(references) or set(references) != set(self.alive)):
            raise ValueError("Present references must identify each living person exactly once")


@dataclass(frozen=True)
class SelfIdentity:
    name: str
    body: str
    reference: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not value.strip()
               for value in (self.name, self.body, self.reference)):
            raise ValueError("Self identity requires original name, body name and current reference")


@dataclass(frozen=True)
class CharacterContext:
    character: str
    persona: tuple[str, ...]
    state: PublicState
    experiences: tuple[MemoryRecord, ...]
    dispositions: tuple[MemoryRecord, ...]
    public_events: tuple[MemoryRecord, ...]
    dialogue: tuple[DialogueLine, ...]
    current_feelings: tuple[CurrentFeeling, ...] = ()
    self_identity: SelfIdentity | None = None

    @property
    def reference(self) -> str:
        return self.self_identity.reference if self.self_identity is not None else self.character

    def __post_init__(self):
        for name in ("persona", "experiences", "dispositions", "public_events", "dialogue", "current_feelings"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if self.self_identity is not None and not isinstance(self.self_identity, SelfIdentity):
            raise ValueError("Self identity must be a typed snapshot")
        if any(not isinstance(feeling, CurrentFeeling) for feeling in self.current_feelings):
            raise ValueError("Current feelings must be typed snapshots")


@dataclass(frozen=True)
class SpeechProposal:
    addressee: str | None
    speech: str


def _append_public_state(parent: ET.Element, state: PublicState) -> None:
    if state.people_present:
        parent.append(ET.fromstring(APPEARANCE_LABELS))
        people = ET.SubElement(parent, "people_present")
        for person in state.people_present:
            ET.SubElement(people, "person", {"name": person.name, "reference": person.reference})
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
        attributes = {"round": str(line.round_number), "speaker": line.speaker}
        if line.addressee is not None:
            attributes["addressee"] = line.addressee
        ET.SubElement(
            section,
            "utterance",
            attributes,
        ).text = line.text


def render_character_context(context: CharacterContext) -> str:
    root = ET.Element(
        "character_context",
        {"character": context.reference, "round": str(context.state.round_number)},
    )
    if context.self_identity is not None:
        identity = context.self_identity
        ET.SubElement(root, "self", {"name": identity.name, "body": identity.body,
                                    "reference": identity.reference})
    persona = ET.SubElement(root, "persona")
    for trait in context.persona:
        ET.SubElement(persona, "trait").text = trait
    _append_public_state(root, context.state)
    _append_records(
        root, "private_experiences", context.experiences, "experience", context.reference
    )
    _append_records(
        root, "dispositions", context.dispositions, "disposition", context.reference
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
