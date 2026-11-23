"""Inventory ownership, pool settings and repeat-safe room offers."""

from dataclasses import FrozenInstanceError
import random
import unittest

from items import ITEM_CATALOGUE, RARITY_WEIGHTS, DraftState, Inventory, InventoryError, InventorySnapshot, eligible_item_pool


class InventoryTests(unittest.TestCase):
    def test_default_pool_cannot_be_disabled_and_unlocks_require_permission(self):
        defaults = ("banana", "muffle", "spare_paper", "throw_voice", "dark_whisper", "love_potion", "forged_note",
                    "divine_revelation", "mail_in_ballot", "watch_this_drive", "room_reservation", "lucky_match", "deep_pockets",
                    "pocket_gavel", "listening_glass", "stay_of_execution", "lucky_coin", "soul")
        with_guardian = (*defaults[:3], "guardian_angel", *defaults[3:])
        self.assertEqual(eligible_item_pool(), defaults)
        self.assertEqual(eligible_item_pool((), ("guardian_angel",)), defaults)
        self.assertEqual(eligible_item_pool(("guardian_angel",), ()), defaults)
        self.assertEqual(eligible_item_pool(("guardian_angel",)), with_guardian)
        self.assertEqual(eligible_item_pool(("guardian_angel", "future_item"), ("guardian_angel",)),
                         with_guardian)

    def test_inventory_hoards_distinct_instances_without_auto_activation(self):
        inventory = Inventory("player")
        first = inventory.grant("guardian_angel")
        second = inventory.grant("guardian_angel")
        banana = inventory.grant("banana")
        self.assertEqual(inventory.entries, (first, second, banana))
        self.assertNotEqual(first.instance_id, second.instance_id)
        self.assertEqual(inventory.consume(second.instance_id), second)
        self.assertEqual(inventory.entries, (first, banana))
        with self.assertRaises(InventoryError):
            inventory.consume(second.instance_id)
        with self.assertRaises(AttributeError):
            inventory.owner_id = "another_owner"
        with self.assertRaises(FrozenInstanceError):
            first.item_id = "banana"

    def test_invalid_grants_and_foreign_instances_do_not_mutate_inventory(self):
        inventory, other = Inventory("player"), Inventory("other")
        own = inventory.grant("banana")
        foreign = other.grant("banana")
        for operation in (lambda: inventory.grant("unknown"),
                          lambda: inventory.get(foreign.instance_id),
                          lambda: inventory.consume("missing")):
            with self.assertRaises(InventoryError):
                operation()
            self.assertEqual(inventory.entries, (own,))

    def test_room_offer_is_stored_and_each_room_rewards_only_once(self):
        inventory = Inventory("player")
        drafts = DraftState("player", eligible_item_pool(), random.Random(3))
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(len(first.options), 3)
        self.assertEqual(len(set(first.options)), 3)
        self.assertIs(drafts.enter_room("Kitchen", inventory.snapshot()), first)
        chosen = drafts.choose(first.offer_id, 1, inventory)
        self.assertEqual(chosen.item_id, first.options[1])
        self.assertIsNone(drafts.pending_offer)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        second = drafts.enter_room("Library", inventory.snapshot())
        self.assertNotEqual(second.offer_id, first.offer_id)
        self.assertEqual(inventory.entries, (chosen,))
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        self.assertEqual(inventory.entries, (chosen,))

    def test_cannot_leave_an_unresolved_offer_or_reopen_a_skipped_reward(self):
        inventory = Inventory("player")
        drafts = DraftState("player", eligible_item_pool(), random.Random(4))
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        with self.assertRaises(InventoryError):
            drafts.enter_room("Library", inventory.snapshot())
        self.assertIs(drafts.pending_offer, first)
        drafts.skip(first.offer_id)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertEqual(drafts.seen_rooms, frozenset({"Kitchen"}))
        with self.assertRaises(InventoryError):
            drafts.skip(first.offer_id)

    def test_one_slot_reroll_changes_only_that_slot_and_is_once_per_run(self):
        pool = eligible_item_pool(("guardian_angel",))
        drafts = DraftState("player", pool, random.Random(5))
        inventory = Inventory("player")
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        rerolled = drafts.reroll(first.offer_id, 1, inventory.snapshot())
        self.assertNotEqual(rerolled.offer_id, first.offer_id)
        self.assertEqual(rerolled.room_id, first.room_id)
        self.assertEqual((rerolled.options[0], rerolled.options[2]), (first.options[0], first.options[2]))
        self.assertNotEqual(rerolled.options[1], first.options[1])
        self.assertEqual(len(set(rerolled.options)), 3)
        self.assertEqual(drafts.rerolls_remaining, 0)
        with self.assertRaises(InventoryError):
            drafts.reroll(rerolled.offer_id, 0, inventory.snapshot())
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        chosen = drafts.choose(rerolled.offer_id, 1, inventory)
        self.assertEqual(chosen.item_id, rerolled.options[1])
        next_offer = drafts.enter_room("Library", inventory.snapshot())
        with self.assertRaises(InventoryError):
            drafts.reroll(next_offer.offer_id, 0, inventory.snapshot())
        self.assertEqual(drafts.rerolls_remaining, 0)

    def test_exhausted_pool_does_not_spend_reroll_or_change_offer(self):
        inventory = Inventory("player")
        drafts = DraftState("player", ("banana", "muffle", "spare_paper"), random.Random(6))
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        with self.assertRaises(InventoryError):
            drafts.reroll(offer.offer_id, 0, inventory.snapshot())
        self.assertEqual(drafts.rerolls_remaining, 1)
        self.assertIs(drafts.pending_offer, offer)

    def test_pool_is_frozen_for_run_and_small_pools_do_not_duplicate_options(self):
        inventory = Inventory("player")
        source = ["banana", "muffle"]
        drafts = DraftState("player", source, random.Random(7))
        source.append("guardian_angel")
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(set(offer.options), {"banana", "muffle"})
        self.assertEqual(drafts.eligible_item_ids, ("banana", "muffle"))
        with self.assertRaises(AttributeError):
            drafts.eligible_item_ids = ("guardian_angel",)

    def test_invalid_slot_stale_offer_and_wrong_owner_do_not_grant_or_consume(self):
        drafts = DraftState("player", eligible_item_pool(("guardian_angel",)), random.Random(8))
        inventory, other = Inventory("player"), Inventory("other")
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        for slot in (-1, 3, None, True, "1"):
            with self.subTest(slot=slot), self.assertRaises(InventoryError):
                drafts.choose(offer.offer_id, slot, inventory)
            with self.subTest(slot=slot), self.assertRaises(InventoryError):
                drafts.reroll(offer.offer_id, slot, inventory.snapshot())
        for operation in (lambda: drafts.choose("stale", 0, inventory),
                          lambda: drafts.reroll("stale", 0, inventory.snapshot()),
                          lambda: drafts.skip("stale"),
                          lambda: drafts.choose(offer.offer_id, 0, other)):
            with self.assertRaises(InventoryError):
                operation()
        self.assertEqual(inventory.entries, ())
        self.assertEqual(other.entries, ())
        self.assertIs(drafts.pending_offer, offer)
        self.assertEqual(drafts.rerolls_remaining, 1)

    def test_extra_pick_has_three_distinct_choices_and_invalidates_each_previous_offer(self):
        drafts = DraftState("player", eligible_item_pool(("guardian_angel",)), random.Random(9))
        inventory = Inventory("player")
        drafts.arm_extra_pick()
        self.assertTrue(drafts.extra_pick_armed)
        with self.assertRaises(InventoryError):
            drafts.arm_extra_pick()
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(first.picks_remaining, 3)
        self.assertFalse(drafts.extra_pick_armed)
        taken = drafts.choose(first.offer_id, 1, inventory)
        second = drafts.pending_offer
        self.assertIsNotNone(second)
        self.assertNotEqual(second.offer_id, first.offer_id)
        self.assertEqual(second.picks_remaining, 2)
        self.assertEqual(second.picked_item_ids, (taken.item_id,))
        self.assertNotIn(taken.item_id, second.options)
        for operation in (lambda: drafts.choose(first.offer_id, 0, inventory),
                          lambda: drafts.reroll(first.offer_id, 0, inventory.snapshot()),
                          lambda: drafts.skip(first.offer_id)):
            with self.assertRaises(InventoryError):
                operation()
        rerolled = drafts.reroll(second.offer_id, 0, inventory.snapshot())
        self.assertNotIn(taken.item_id, rerolled.options)
        self.assertEqual(rerolled.picks_remaining, 2)
        other = drafts.choose(rerolled.offer_id, 0, inventory)
        self.assertNotEqual(taken.item_id, other.item_id)
        third = drafts.choose(drafts.pending_offer.offer_id, 0, inventory)
        self.assertEqual(len({taken.item_id, other.item_id, third.item_id}), 3)
        self.assertEqual(inventory.entries, (taken, other, third))
        self.assertIsNone(drafts.pending_offer)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertEqual(drafts.enter_room("Library", inventory.snapshot()).picks_remaining, 1)

    def test_three_pick_offer_clamps_remaining_choices_when_holdings_change(self):
        inventory = Inventory("player")
        drafts = DraftState("player", eligible_item_pool(), random.Random(23))
        drafts.arm_extra_pick()
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        inventory.grant(offer.options[2])
        drafts.choose(offer.offer_id, 0, inventory)
        remaining = drafts.pending_offer
        self.assertEqual(remaining.picks_remaining, 1)
        self.assertEqual(remaining.options, (offer.options[1],))
        drafts.choose(remaining.offer_id, 0, inventory)
        self.assertIsNone(drafts.pending_offer)

    def test_extra_pick_can_be_skipped_after_one_and_cannot_restore_the_first_offer(self):
        drafts = DraftState("player", ("banana", "muffle", "spare_paper"), random.Random(10))
        inventory = Inventory("player")
        drafts.arm_extra_pick()
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        first = drafts.choose(offer.offer_id, 0, inventory)
        remaining = drafts.pending_offer
        # Already selected items cannot reappear as reroll alternatives.
        with self.assertRaises(InventoryError):
            drafts.reroll(remaining.offer_id, 0, inventory.snapshot())
        self.assertEqual(drafts.rerolls_remaining, 1)
        drafts.skip(remaining.offer_id)
        self.assertEqual(inventory.entries, (first,))
        self.assertIsNone(drafts.pending_offer)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))

    def test_extra_pick_arms_for_a_new_offer_and_requires_two_pool_items(self):
        inventory = Inventory("player")
        small = DraftState("player", ("banana",))
        with self.assertRaises(InventoryError):
            small.arm_extra_pick()
        self.assertFalse(small.extra_pick_armed)
        drafts = DraftState("player", eligible_item_pool(), random.Random(11))
        current = drafts.enter_room("Kitchen", inventory.snapshot())
        drafts.arm_extra_pick()
        self.assertEqual(current.picks_remaining, 1)
        drafts.skip(current.offer_id)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertTrue(drafts.extra_pick_armed)
        self.assertEqual(drafts.enter_room("Library", inventory.snapshot()).picks_remaining, 3)

    def test_added_reroll_charges_can_be_spent_without_reopening_a_room(self):
        inventory = Inventory("player")
        drafts = DraftState("player", eligible_item_pool(("guardian_angel",)), random.Random(12))
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        for count in (0, -1, True, 1.5, "1"):
            with self.assertRaises(InventoryError):
                drafts.add_rerolls(count)
        self.assertEqual(drafts.rerolls_remaining, 1)
        drafts.add_rerolls(2)
        for _ in range(3):
            offer = drafts.reroll(offer.offer_id, 0, inventory.snapshot())
        self.assertEqual(drafts.rerolls_remaining, 0)
        with self.assertRaises(InventoryError):
            drafts.reroll(offer.offer_id, 0, inventory.snapshot())
        drafts.skip(offer.offer_id)
        drafts.add_rerolls()
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertEqual(drafts.rerolls_remaining, 1)

    def test_snapshot_is_immutable_validated_and_copies_current_holdings(self):
        inventory = Inventory("player")
        source = ["banana"]
        supplied = InventorySnapshot("player", source)
        source.append("muffle")
        self.assertEqual(supplied.item_ids, frozenset({"banana"}))
        empty = inventory.snapshot()
        inventory.grant("banana")
        self.assertFalse(empty.item_ids)
        self.assertEqual(inventory.snapshot().item_ids, supplied.item_ids)
        with self.assertRaises(FrozenInstanceError):
            supplied.owner_id = "other"
        for owner, ids in (("", ()), (None, ()), ("player", "banana"),
                           ("player", ("missing",)), ("player", (None,))):
            with self.subTest(owner=owner, ids=ids), self.assertRaises(InventoryError):
                InventorySnapshot(owner, ids)

    def test_current_holdings_reduce_offers_and_consumed_types_can_return(self):
        inventory = Inventory("player")
        banana = inventory.grant("banana")
        inventory.grant("muffle")
        drafts = DraftState("player", ("banana", "muffle", "spare_paper"))
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(offer.options, ("spare_paper",))
        drafts.choose(offer.offer_id, 0, inventory)
        self.assertIsNone(drafts.enter_room("Cellar", inventory.snapshot()))
        self.assertIsNone(drafts.pending_offer)
        self.assertIn("Cellar", drafts.seen_rooms)
        inventory.consume(banana.instance_id)
        returned = drafts.enter_room("Library", inventory.snapshot())
        self.assertEqual(returned.options, ("banana",))
        self.assertEqual(drafts.eligible_item_ids, ("banana", "muffle", "spare_paper"))

    def test_wrong_snapshot_owner_or_invalid_snapshot_never_opens_or_rerolls(self):
        drafts = DraftState("player", eligible_item_pool())
        inventory = Inventory("player")
        for invalid in (None, inventory, Inventory("other").snapshot()):
            with self.subTest(invalid=invalid), self.assertRaises(InventoryError):
                drafts.enter_room("Kitchen", invalid)
            self.assertFalse(drafts.seen_rooms)
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        for invalid in (None, inventory, Inventory("other").snapshot()):
            with self.subTest(invalid=invalid), self.assertRaises(InventoryError):
                drafts.reroll(offer.offer_id, 0, invalid)
            self.assertIs(drafts.pending_offer, offer)
            self.assertEqual(drafts.rerolls_remaining, 1)

    def test_reroll_excludes_held_alternatives_and_preserves_charge_if_exhausted(self):
        inventory = Inventory("player")
        drafts = DraftState("player", ("banana", "muffle", "spare_paper", "forged_note"), random.Random(3))
        offer = drafts.enter_room("Kitchen", inventory.snapshot())
        alternative = next(item_id for item_id in drafts.eligible_item_ids if item_id not in offer.options)
        held = inventory.grant(alternative)
        self.assertFalse(drafts.reroll_alternatives(offer.offer_id, inventory.snapshot()))
        with self.assertRaises(InventoryError):
            drafts.reroll(offer.offer_id, 0, inventory.snapshot())
        self.assertIs(drafts.pending_offer, offer)
        self.assertEqual(drafts.rerolls_remaining, 1)
        inventory.consume(held.instance_id)
        changed = drafts.reroll(offer.offer_id, 0, inventory.snapshot())
        self.assertEqual(changed.options[0], alternative)
        with self.assertRaises(InventoryError):
            drafts.reroll_alternatives(offer.offer_id, inventory.snapshot())

    def test_second_pick_and_choose_recheck_live_inventory_and_old_offer_ids(self):
        inventory = Inventory("player")
        drafts = DraftState("player", eligible_item_pool(), random.Random(4))
        drafts.arm_extra_pick()
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        acquired_elsewhere = inventory.grant(first.options[1])
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        self.assertIs(drafts.pending_offer, first)
        self.assertEqual(inventory.entries, (acquired_elsewhere,))
        chosen = drafts.choose(first.offer_id, 0, inventory)
        second = drafts.pending_offer
        self.assertEqual(second.options, (first.options[2],))
        self.assertEqual(second.picks_remaining, 1)
        self.assertTrue(set(second.options).isdisjoint(inventory.snapshot().item_ids))
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 2, inventory)
        drafts.choose(second.offer_id, 0, inventory)
        self.assertEqual(len(inventory.entries), 3)
        self.assertIn(chosen, inventory.entries)
        self.assertIsNone(drafts.pending_offer)

    def test_pending_offer_refresh_removes_newly_owned_types_without_refunding_room(self):
        inventory = Inventory("player")
        drafts = DraftState("player", ("banana", "muffle", "spare_paper"))
        first = drafts.enter_room("Kitchen", inventory.snapshot())
        inventory.grant(first.options[0])
        refreshed = drafts.enter_room("Kitchen", inventory.snapshot())
        self.assertNotEqual(refreshed.offer_id, first.offer_id)
        self.assertEqual(refreshed.options, first.options[1:])
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        for item_id in refreshed.options:
            inventory.grant(item_id)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertIsNone(drafts.pending_offer)
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))

    def test_exhausted_room_does_not_spend_an_armed_extra_pick(self):
        inventory = Inventory("player")
        first = inventory.grant("banana")
        second = inventory.grant("muffle")
        drafts = DraftState("player", ("banana", "muffle"))
        drafts.arm_extra_pick()
        self.assertIsNone(drafts.enter_room("Kitchen", inventory.snapshot()))
        self.assertTrue(drafts.extra_pick_armed)
        inventory.consume(first.instance_id)
        inventory.consume(second.instance_id)
        self.assertEqual(drafts.enter_room("Library", inventory.snapshot()).picks_remaining, 2)


class ScriptedRng:
    """Exact boundary rolls with deterministic within-tier selection."""
    def __init__(self, *rolls):
        self.rolls = iter(rolls)
        self.choices = []

    def random(self):
        return next(self.rolls)

    def choice(self, values):
        self.choices.append(tuple(values))
        return values[0]


class RarityDraftTests(unittest.TestCase):
    def test_catalogue_uses_the_approved_tier_assignments(self):
        expected = {
            "green": {"banana", "muffle", "spare_paper", "forged_note", "room_reservation",
                      "lucky_match", "deep_pockets", "pocket_gavel"},
            "blue": {"throw_voice", "dark_whisper", "love_potion", "divine_revelation", "mail_in_ballot",
                     "watch_this_drive", "listening_glass", "stay_of_execution", "lucky_coin"},
            "purple": {"booby_trap", "spin_the_dagger", "presidential_decree", "occult_ritual", "revolver"},
            "gold": {"guardian_angel", "mass_discombobulator", "soul"},
        }
        actual = {tier: {item_id for item_id, item in ITEM_CATALOGUE.items() if item.rarity == tier}
                  for tier in RARITY_WEIGHTS}
        self.assertEqual(actual, expected)

    def test_exact_tier_boundaries_and_within_tier_candidates(self):
        self.assertEqual(dict(RARITY_WEIGHTS), {"green": 40, "blue": 30, "purple": 20, "gold": 10})
        pool = ("banana", "muffle", "throw_voice", "dark_whisper", "booby_trap", "revolver", "guardian_angel")
        for roll, tier, candidates in ((0, "green", ("banana", "muffle")),
                                       (.399999, "green", ("banana", "muffle")),
                                       (.4, "blue", ("throw_voice", "dark_whisper")),
                                       (.699999, "blue", ("throw_voice", "dark_whisper")),
                                       (.7, "purple", ("booby_trap", "revolver")),
                                       (.899999, "purple", ("booby_trap", "revolver")),
                                       (.9, "gold", ("guardian_angel",)),
                                       (.999999, "gold", ("guardian_angel",))):
            with self.subTest(roll=roll):
                rng = ScriptedRng(roll, 0, 0)
                draft = DraftState("player", pool, rng)
                offer = draft.enter_room("Kitchen", Inventory("player").snapshot())
                self.assertEqual(ITEM_CATALOGUE[offer.options[0]].rarity, tier)
                self.assertEqual(rng.choices[0], candidates)
                self.assertEqual(len(set(offer.options)), 3)

    def test_empty_tiers_normalize_remaining_weights(self):
        pool = ("throw_voice", "dark_whisper", "love_potion", "guardian_angel")
        # Blue and gold retain relative weights 30:10 after green/purple vanish.
        for roll, expected in ((.749999, "blue"), (.75, "gold")):
            with self.subTest(roll=roll):
                draft = DraftState("player", pool, ScriptedRng(roll, 0, 0))
                offer = draft.enter_room("Kitchen", Inventory("player").snapshot())
                self.assertEqual(ITEM_CATALOGUE[offer.options[0]].rarity, expected)

    def test_depleted_tiers_are_removed_between_slots_and_held_types_excluded(self):
        inventory = Inventory("player")
        inventory.grant("muffle")
        rng = ScriptedRng(0, 0, 0)
        draft = DraftState("player", ("muffle", "banana", "throw_voice", "guardian_angel"), rng)
        offer = draft.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(offer.options, ("banana", "throw_voice", "guardian_angel"))
        self.assertEqual(rng.choices, [("banana",), ("throw_voice",), ("guardian_angel",)])

    def test_reroll_has_same_tier_and_locked_slot_does_not_spend_charge(self):
        inventory = Inventory("player")
        draft = DraftState("player", ("banana", "muffle", "spare_paper", "forged_note", "guardian_angel"),
                           ScriptedRng(.99, 0, 0))
        offer = draft.enter_room("Kitchen", inventory.snapshot())
        self.assertEqual(offer.options[0], "guardian_angel")
        self.assertFalse(draft.reroll_alternatives(offer.offer_id, inventory.snapshot(), slot_index=0))
        self.assertTrue(draft.reroll_alternatives(offer.offer_id, inventory.snapshot(), slot_index=1))
        with self.assertRaises(InventoryError):
            draft.reroll(offer.offer_id, 0, inventory.snapshot())
        self.assertEqual(draft.rerolls_remaining, 1)
        self.assertIs(draft.pending_offer, offer)
        replacement = draft.reroll(offer.offer_id, 1, inventory.snapshot())
        self.assertEqual(ITEM_CATALOGUE[replacement.options[1]].rarity, "green")
        self.assertEqual((replacement.options[0], replacement.options[2]), (offer.options[0], offer.options[2]))
        self.assertNotEqual(replacement.offer_id, offer.offer_id)
        self.assertEqual(draft.rerolls_remaining, 0)

    def test_consumed_pick_cannot_return_during_same_multi_pick_offer(self):
        inventory = Inventory("player")
        draft = DraftState("player", ("banana", "muffle", "spare_paper", "forged_note"), ScriptedRng(0, 0, 0))
        draft.arm_extra_pick()
        offer = draft.enter_room("Kitchen", inventory.snapshot())
        picked = draft.choose(offer.offer_id, 0, inventory)
        inventory.consume(picked.instance_id)
        remaining = draft.pending_offer
        self.assertEqual(remaining.picks_remaining, 2)
        self.assertEqual(draft.reroll_alternatives(remaining.offer_id, inventory.snapshot(), slot_index=0), ("forged_note",))
        updated = draft.reroll(remaining.offer_id, 0, inventory.snapshot())
        self.assertEqual(updated.picked_item_ids, (picked.item_id,))
        self.assertEqual(updated.picks_remaining, 2)
        self.assertNotIn(picked.item_id, updated.options)
        with self.assertRaises(InventoryError):
            draft.choose(remaining.offer_id, 0, inventory)


if __name__ == "__main__":
    unittest.main()
