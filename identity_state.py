"""Stable mind ownership and body occupancy, independent of narrative rendering."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class IdentityState:
    player_mind_id: str
    occupants: tuple[tuple[str, str], ...]

    def __post_init__(self):
        pairs = tuple(tuple(pair) for pair in self.occupants)
        if (not self.player_mind_id or not pairs
                or any(len(pair) != 2 or any(not isinstance(value, str) or not value.strip()
                                            for value in pair) for pair in pairs)
                or len({body for body, _ in pairs}) != len(pairs)
                or len({mind for _, mind in pairs}) != len(pairs)
                or self.player_mind_id not in {mind for _, mind in pairs}):
            raise ValueError("Each body and mind requires one unique occupant relationship")
        object.__setattr__(self, "occupants", pairs)

    @classmethod
    def from_bodies(cls, player_mind_id, player_body_id, npc_body_ids):
        return cls(player_mind_id, ((player_body_id, player_mind_id),
                                   *((body_id, body_id) for body_id in npc_body_ids)))

    def body_for_mind(self, mind_id):
        return next((body for body, mind in self.occupants if mind == mind_id), None)

    def mind_for_body(self, body_id):
        return next((mind for body, mind in self.occupants if body == body_id), None)

    @property
    def player_body_id(self):
        return self.body_for_mind(self.player_mind_id)

    def exchange(self, target_body_id):
        target_mind = self.mind_for_body(target_body_id)
        if target_mind is None or target_mind == self.player_mind_id:
            raise ValueError("An exchange requires another occupied body")
        old_body = self.player_body_id
        return replace(self, occupants=tuple(
            (body, target_mind if body == old_body else self.player_mind_id
             if body == target_body_id else mind) for body, mind in self.occupants))
