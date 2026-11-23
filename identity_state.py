"""Stable mind ownership and body occupancy, independent of narrative rendering."""

from dataclasses import dataclass, replace


@dataclass(frozen=True)
class IdentityState:
    """Authoritative mutations advance revision, including visually identical copies."""

    player_mind_id: str
    occupants: tuple[tuple[str, str], ...]
    appearances: tuple[tuple[str, str], ...] = ()
    revision: int = 0

    def __post_init__(self):
        if type(self.revision) is not int or self.revision < 0:
            raise ValueError("An identity revision must be a nonnegative integer")
        pairs = tuple(tuple(pair) for pair in self.occupants)
        if (not self.player_mind_id or not pairs
                or any(len(pair) != 2 or any(not isinstance(value, str) or not value.strip()
                                            for value in pair) for pair in pairs)
                or len({body for body, _ in pairs}) != len(pairs)
                or len({mind for _, mind in pairs}) != len(pairs)
                or self.player_mind_id not in {mind for _, mind in pairs}):
            raise ValueError("Each body and mind requires one unique occupant relationship")
        appearances = tuple(tuple(pair) for pair in self.appearances)
        if (any(len(pair) != 2 or any(not isinstance(value, str) or not value.strip()
                                     for value in pair) for pair in appearances)
                or len({body for body, _ in appearances}) != len(appearances)
                or not {body for body, _ in appearances}.issubset({body for body, _ in pairs})):
            raise ValueError("Appearance names require unique known bodies and nonempty names")
        object.__setattr__(self, "occupants", pairs)
        object.__setattr__(self, "appearances", appearances)

    @classmethod
    def from_bodies(cls, player_mind_id, player_body_id, npc_body_ids, *, appearance_names=None):
        return cls(player_mind_id, ((player_body_id, player_mind_id),
                                   *((body_id, body_id) for body_id in npc_body_ids)),
                   tuple((appearance_names or {}).items()))

    def appearance_name(self, body_id):
        return next((name for body, name in self.appearances if body == body_id), None)

    def clone_appearance(self, actor_body_id, target_body_id):
        name = self.appearance_name(target_body_id)
        if (self.mind_for_body(actor_body_id) is None or actor_body_id == target_body_id
                or name is None):
            raise ValueError("Appearance cloning requires distinct known bodies and a named target")
        appearances = dict(self.appearances)
        appearances[actor_body_id] = name
        return replace(self, appearances=tuple(appearances.items()), revision=self.revision + 1)

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
        return replace(self, revision=self.revision + 1, occupants=tuple(
            (body, target_mind if body == old_body else self.player_mind_id
             if body == target_body_id else mind) for body, mind in self.occupants))
