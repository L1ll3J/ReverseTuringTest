"""Inventory ownership, pool settings and repeat-safe room offers."""

from dataclasses import FrozenInstanceError
import random
import unittest

from items import DraftState, Inventory, InventoryError, eligible_item_pool


class InventoryTests(unittest.TestCase):
    def test_default_pool_cannot_be_disabled_and_unlocks_require_permission(self):
        defaults = ("banana", "muffle", "spare_paper")
        self.assertEqual(eligible_item_pool(), defaults)
        self.assertEqual(eligible_item_pool((), ("guardian_angel",)), defaults)
        self.assertEqual(eligible_item_pool(("guardian_angel",), ()), defaults)
        self.assertEqual(eligible_item_pool(("guardian_angel",)), (*defaults, "guardian_angel"))
        self.assertEqual(eligible_item_pool(("guardian_angel", "future_item"), ("guardian_angel",)),
                         (*defaults, "guardian_angel"))

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
        first = drafts.enter_room("Kitchen")
        self.assertEqual(len(first.options), 3)
        self.assertEqual(len(set(first.options)), 3)
        self.assertIs(drafts.enter_room("Kitchen"), first)
        chosen = drafts.choose(first.offer_id, 1, inventory)
        self.assertEqual(chosen.item_id, first.options[1])
        self.assertIsNone(drafts.pending_offer)
        self.assertIsNone(drafts.enter_room("Kitchen"))
        second = drafts.enter_room("Library")
        self.assertNotEqual(second.offer_id, first.offer_id)
        self.assertEqual(inventory.entries, (chosen,))
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        self.assertEqual(inventory.entries, (chosen,))

    def test_cannot_leave_an_unresolved_offer_or_reopen_a_skipped_reward(self):
        drafts = DraftState("player", eligible_item_pool(), random.Random(4))
        first = drafts.enter_room("Kitchen")
        with self.assertRaises(InventoryError):
            drafts.enter_room("Library")
        self.assertIs(drafts.pending_offer, first)
        drafts.skip(first.offer_id)
        self.assertIsNone(drafts.enter_room("Kitchen"))
        self.assertEqual(drafts.seen_rooms, frozenset({"Kitchen"}))
        with self.assertRaises(InventoryError):
            drafts.skip(first.offer_id)

    def test_one_slot_reroll_changes_only_that_slot_and_is_once_per_run(self):
        pool = eligible_item_pool(("guardian_angel",))
        drafts = DraftState("player", pool, random.Random(5))
        inventory = Inventory("player")
        first = drafts.enter_room("Kitchen")
        rerolled = drafts.reroll(first.offer_id, 1)
        self.assertNotEqual(rerolled.offer_id, first.offer_id)
        self.assertEqual(rerolled.room_id, first.room_id)
        self.assertEqual((rerolled.options[0], rerolled.options[2]), (first.options[0], first.options[2]))
        self.assertNotEqual(rerolled.options[1], first.options[1])
        self.assertEqual(len(set(rerolled.options)), 3)
        self.assertEqual(drafts.rerolls_remaining, 0)
        with self.assertRaises(InventoryError):
            drafts.reroll(rerolled.offer_id, 0)
        with self.assertRaises(InventoryError):
            drafts.choose(first.offer_id, 1, inventory)
        chosen = drafts.choose(rerolled.offer_id, 1, inventory)
        self.assertEqual(chosen.item_id, rerolled.options[1])
        next_offer = drafts.enter_room("Library")
        with self.assertRaises(InventoryError):
            drafts.reroll(next_offer.offer_id, 0)
        self.assertEqual(drafts.rerolls_remaining, 0)

    def test_exhausted_pool_does_not_spend_reroll_or_change_offer(self):
        drafts = DraftState("player", eligible_item_pool(), random.Random(6))
        offer = drafts.enter_room("Kitchen")
        with self.assertRaises(InventoryError):
            drafts.reroll(offer.offer_id, 0)
        self.assertEqual(drafts.rerolls_remaining, 1)
        self.assertIs(drafts.pending_offer, offer)

    def test_pool_is_frozen_for_run_and_small_pools_do_not_duplicate_options(self):
        source = ["banana", "muffle"]
        drafts = DraftState("player", source, random.Random(7))
        source.append("guardian_angel")
        offer = drafts.enter_room("Kitchen")
        self.assertEqual(set(offer.options), {"banana", "muffle"})
        self.assertEqual(drafts.eligible_item_ids, ("banana", "muffle"))
        with self.assertRaises(AttributeError):
            drafts.eligible_item_ids = ("guardian_angel",)

    def test_invalid_slot_stale_offer_and_wrong_owner_do_not_grant_or_consume(self):
        drafts = DraftState("player", eligible_item_pool(("guardian_angel",)), random.Random(8))
        inventory, other = Inventory("player"), Inventory("other")
        offer = drafts.enter_room("Kitchen")
        for slot in (-1, 3, None, True, "1"):
            with self.subTest(slot=slot), self.assertRaises(InventoryError):
                drafts.choose(offer.offer_id, slot, inventory)
            with self.subTest(slot=slot), self.assertRaises(InventoryError):
                drafts.reroll(offer.offer_id, slot)
        for operation in (lambda: drafts.choose("stale", 0, inventory),
                          lambda: drafts.reroll("stale", 0),
                          lambda: drafts.skip("stale"),
                          lambda: drafts.choose(offer.offer_id, 0, other)):
            with self.assertRaises(InventoryError):
                operation()
        self.assertEqual(inventory.entries, ())
        self.assertEqual(other.entries, ())
        self.assertIs(drafts.pending_offer, offer)
        self.assertEqual(drafts.rerolls_remaining, 1)


if __name__ == "__main__":
    unittest.main()
