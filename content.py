"""Production content and human-readable unlock goals."""

from progression import ContentDefinition, UnlockRule
from items import ITEM_CATALOGUE


UNLOCK_GOALS = {
    "bare": "Available from the start.",
    "knife": "Kill your first victim.",
    "pen": "Survive a vote.",
    "c4": "Kill three victims in one run.",
    "love": "Give someone a Banana of Friendship.",
    "blackmagic": "Have two innocent suspects executed in one run.",
    "stopwatch": "Survive a vote while a living witness has seen you kill.",
    "voodoo": "Use Soul Transfusion.",
    "guardian_angel": "Survive a vote.",
    "booby_trap": "Kill the suspect marked with Watch This Drive in the next action phase.",
    "presidential_decree": "Survive a vote where your Spare Paper copies counted.",
    "occult_ritual": "Kill a suspect in the room you assigned them with Room Reservation.",
    "spin_the_dagger": "Win a run after using three different item types.",
    "revolver": "Kill three victims in one run.",
    "lucky_coin": "Survive a Guardian Angel intervention and finish that run alive.",
}

ITEM_UNLOCK_RULES = {
    "guardian_angel": UnlockRule("survived_vote", 1),
    "booby_trap": UnlockRule("marked_kill_completed", 1),
    "presidential_decree": UnlockRule("survived_spare_paper_vote", 1),
    "occult_ritual": UnlockRule("reserved_room_kill", 1),
    "spin_the_dagger": UnlockRule("three_item_types_victory", 1),
    "revolver": UnlockRule("three_kills_one_run", 1),
    "lucky_coin": UnlockRule("guardian_intervention_run_survived", 1),
}

CONTENT_CATALOGUE = (
    ContentDefinition("bare", "weapon", default=True),
    ContentDefinition("knife", "weapon", unlock_rule=UnlockRule("player_kill", 1)),
    ContentDefinition("pen", "weapon", unlock_rule=UnlockRule("survived_vote", 1)),
    ContentDefinition("c4", "weapon", unlock_rule=UnlockRule("three_kills_one_run", 1)),
    ContentDefinition("love", "weapon", unlock_rule=UnlockRule("banana_given", 1)),
    ContentDefinition("blackmagic", "weapon", unlock_rule=UnlockRule("two_innocents_executed", 1)),
    ContentDefinition("stopwatch", "weapon", unlock_rule=UnlockRule("survived_witnessed_vote", 1)),
    ContentDefinition("voodoo", "weapon", unlock_rule=UnlockRule("soul_transfusion_used", 1)),
) + tuple(
    ContentDefinition(item_id, "item", default=item.default,
                      unlock_rule=None if item.default else ITEM_UNLOCK_RULES[item_id])
    for item_id, item in ITEM_CATALOGUE.items()
)


def available_content(profile, category):
    return tuple(content.content_id for content in CONTENT_CATALOGUE
                 if content.category == category and (content.default or content.content_id in profile.unlocked)
                 and (content.default or category != "item" or content.content_id not in profile.disabled_items))
