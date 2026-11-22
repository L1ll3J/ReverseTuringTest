"""Production content and human-readable unlock goals."""

from progression import ContentDefinition, UnlockRule


UNLOCK_GOALS = {
    "bare": "Available from the start.",
    "knife": "Kill your first victim.",
    "pen": "Survive a vote.",
    "c4": "Kill three victims across your runs.",
    "love": "Give someone a Banana of Friendship.",
    "blackmagic": "Have two innocent suspects executed in one run.",
    "stopwatch": "Survive a vote while a living witness has seen you kill.",
    "voodoo": "Win with Bare Hands without using any items.",
    "guardian_angel": "Survive a vote.",
}

CONTENT_CATALOGUE = (
    ContentDefinition("bare", "weapon", default=True),
    ContentDefinition("knife", "weapon", unlock_rule=UnlockRule("player_kill", 1)),
    ContentDefinition("pen", "weapon", unlock_rule=UnlockRule("survived_vote", 1)),
    ContentDefinition("c4", "weapon", unlock_rule=UnlockRule("player_kill", 3)),
    ContentDefinition("love", "weapon", unlock_rule=UnlockRule("banana_given", 1)),
    ContentDefinition("blackmagic", "weapon", unlock_rule=UnlockRule("two_innocents_executed", 1)),
    ContentDefinition("stopwatch", "weapon", unlock_rule=UnlockRule("survived_witnessed_vote", 1)),
    ContentDefinition("voodoo", "weapon", unlock_rule=UnlockRule("bare_hands_no_item_victory", 1)),
    ContentDefinition("banana", "item", default=True),
    ContentDefinition("muffle", "item", default=True),
    ContentDefinition("spare_paper", "item", default=True),
    ContentDefinition("guardian_angel", "item", unlock_rule=UnlockRule("survived_vote", 1)),
)


def available_content(profile, category):
    return tuple(content.content_id for content in CONTENT_CATALOGUE
                 if content.category == category and (content.default or content.content_id in profile.unlocked)
                 and (content.default or category != "item" or content.content_id not in profile.disabled_items))
