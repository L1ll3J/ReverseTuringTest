"""Approved, immutable emotional sounds for characters who cannot speak."""

from types import MappingProxyType


TONES = (-2, -1, 0, 1, 2)
TONE_LABELS = ("furious", "frustrated", "neutral", "receptive", "warmly agreeable")
MUFFLED_BANKS = MappingProxyType({
    "Jerry": ("FMMMPH!! GNNNRR!!", "Mrrmph... ngh!", "Mmph... hnnf.", "Hnmph, mmh.", "Hnnpfh, hnnpfh, hnnpfh."),
    "Dave": ("HMMMPHH!! GRRRR!!", "Hrrmph! Mmph!", "Mmph. Hm.", "Hmmpf, mmhm.", "Mmhmph, mmhmph!"),
    "Hank": ("GRRMMMPHF!! FMMMH!!", "Mmmrrgh! Hnf!", "Hnnf... mmph.", "Hmnph! Hmnph.", "Hnnpfh, hnnpfh! Mmmh!"),
    "Owen": ("FMRRPH!! NNNGH!!", "Hrrmmph... fnf!", "Mmmph... hm.", "Mmmh, hnph.", "Hmmmph, hmmmph, mmmh."),
    "Debra": ("NNNGH!! FMMMRRPH!!", "Hnnrrf... mrrph!", "Hmmm... ffn.", "Mmh... hnnpfh.", "Hnnph, hnnph... mmmh."),
    "fallback": ("FMMMPHH!! GRRHH!!", "Mrrmph! Hnf!", "Mmpphff... hnngg brrrrff gnf.", "Hnmph, mmh.", "Hnnpfh, hnnpfh, hnnpfh."),
})


def response_bank(style):
    return MUFFLED_BANKS.get(style, MUFFLED_BANKS["fallback"])


def muffled_sound(style, tone):
    if type(tone) is not int or tone not in TONES:
        raise ValueError("A muffled response requires one of the five integer tones")
    return response_bank(style)[tone + 2]
