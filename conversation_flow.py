"""Conversation limits and bounded preparation state, independent of Qt and APIs."""

from dataclasses import dataclass, field
from threading import Event

from ai_context import CharacterContext, DialogueLine, MemoryRecord, PublicState, SpeechProposal


class DirectReplyPolicy:
    limit = 3

    def __init__(self):
        self.discussion_id = None
        self.body_id = None
        self.count = 0

    def begin_discussion(self, identifier):
        if identifier != self.discussion_id:
            self.discussion_id = identifier
            self.body_id = None
            self.count = 0

    def count_for(self, body_id):
        return self.count if body_id == self.body_id else 0

    def can_direct(self, body_id):
        return self.count_for(body_id) < self.limit

    def committed(self, body_id, *, direct=False, ordinary=True):
        if not ordinary:
            return
        if direct and not self.can_direct(body_id):
            raise ValueError("This suspect has reached the direct-reply limit")
        if body_id != self.body_id:
            self.body_id = None
            self.count = 0
        if direct:
            self.body_id = body_id
            self.count += 1


@dataclass(frozen=True)
class GroupTurnSnapshot:
    revision: int
    round_number: int
    player_name: str
    contexts: tuple[CharacterContext, ...]
    state: PublicState
    public_events: tuple[MemoryRecord, ...]
    dialogue: tuple[DialogueLine, ...]
    overrides: tuple[tuple[str, SpeechProposal], ...]
    identities: tuple[tuple[str, str], ...]
    effects: tuple
    runtime_signature: tuple

    @property
    def selector_args(self):
        return self.state, self.public_events, self.dialogue


@dataclass
class PreparationTicket:
    identifier: int
    snapshot: GroupTurnSnapshot
    cancelled: Event = field(default_factory=Event)
    workers: int = 0


@dataclass(frozen=True)
class PreparedReply:
    ticket_id: int
    snapshot: GroupTurnSnapshot
    speaker: str
    proposal: SpeechProposal


@dataclass(frozen=True)
class PreparationFailure:
    ticket_id: int
    snapshot: GroupTurnSnapshot
    message: str


class TurnPreparation:
    """One GUI-owned pipeline slot; cancellation is safe to read in workers."""

    def __init__(self):
        self.active = None
        self.ready = None
        self.failure = None
        self._sequence = 0

    def reserve(self, snapshot):
        if self.active is not None or self.ready is not None or self.failure is not None:
            return None
        self._sequence += 1
        self.active = PreparationTicket(self._sequence, snapshot)
        return self.active

    def invalidate(self):
        if self.active is not None:
            self.active.cancelled.set()
        self.ready = None
        self.failure = None

    def current(self, ticket, snapshot):
        return (self.active is ticket and not ticket.cancelled.is_set()
                and ticket.snapshot == snapshot)

    def worker_started(self, ticket):
        if self.active is not ticket:
            raise ValueError("A preparation worker requires the active ticket")
        ticket.workers += 1

    def worker_finished(self, ticket):
        if self.active is not ticket or ticket.workers < 1:
            raise ValueError("A preparation worker may finish only once")
        ticket.workers -= 1
        if ticket.workers == 0:
            self.active = None
            return True
        return False

    def publish(self, ticket, snapshot, speaker, proposal):
        if not self.current(ticket, snapshot):
            return False
        self.ready = PreparedReply(ticket.identifier, snapshot, speaker, proposal)
        return True

    def fail(self, ticket, snapshot, message):
        if not self.current(ticket, snapshot):
            return False
        self.failure = PreparationFailure(ticket.identifier, snapshot, message)
        return True

    def take(self, snapshot):
        if self.ready is None or self.ready.snapshot != snapshot:
            return None
        ready, self.ready = self.ready, None
        return ready
