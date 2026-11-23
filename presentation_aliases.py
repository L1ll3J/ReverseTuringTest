"""Ephemeral UI aliases; body identifiers remain the authoritative action targets."""

from dataclasses import dataclass
import random

from identity_state import IdentityState


@dataclass(frozen=True)
class AliasSnapshot:
    phase_id: str
    entries: tuple[tuple[str, str], ...]

    def label_for_body(self, body_id):
        return next((label for body, label in self.entries if body == body_id), None)

    def body_for_label(self, label):
        return next((body for body, display in self.entries if display == label), None)


class PresentationAliases:
    def __init__(self, rng=None):
        self._rng = rng if rng is not None else random.Random()
        self._snapshots = {}
        self._assignments = {}

    def for_presence(self, phase_id: str, identity: IdentityState, present_body_ids) -> AliasSnapshot:
        bodies = tuple(sorted(present_body_ids))
        if (not isinstance(phase_id, str) or not phase_id.strip()
                or len(set(bodies)) != len(bodies)):
            raise ValueError("A presentation requires a phase and unique present bodies")
        names = tuple((body, identity.appearance_name(body)) for body in bodies)
        if any(name is None for _, name in names):
            raise ValueError("Every presented body requires an explicit canonical appearance name")
        key = phase_id, names
        if key in self._snapshots:
            return self._snapshots[key]
        groups = {}
        for body, name in names:
            groups.setdefault(name, []).append(body)
        labels = {}
        reserved = set(groups)
        assignments = self._assignments.setdefault(phase_id, {})
        for name, members in groups.items():
            if len(members) == 1:
                labels[members[0]] = name
                continue
            assigned = assignments.setdefault(name, {})
            for body in members:
                label = assigned.get(body)
                if label is not None and label not in reserved:
                    labels[body] = label
                    reserved.add(label)
            shuffled = [body for body in members if body not in labels]
            self._rng.shuffle(shuffled)
            number = 1
            for body in shuffled:
                label = f"{name} #{number}"
                while label in reserved or label in assigned.values():
                    number += 1
                    label = f"{name} #{number}"
                labels[body] = label
                assigned[body] = label
                reserved.add(label)
                number += 1
        snapshot = AliasSnapshot(phase_id, tuple((body, labels[body]) for body in bodies))
        self._snapshots[key] = snapshot
        return snapshot
