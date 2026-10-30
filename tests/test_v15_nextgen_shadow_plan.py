import unittest
from tools.v15_nextgen_shadow_plan import observation_key, shadow_plan


def candidate(identity, **extra):
    return dict(cat_side="CRYPTO_LONG", id=identity, switch=identity, eval_cpu_s=1, joint_cpu_s=1, fixed_cpu_s=1, predicted_marginal_delta=1, **extra)


class ShadowPlanTests(unittest.TestCase):
    def plan(self, items, observations=None, **extra):
        return shadow_plan(items, observations or {}, era="engine1:wiring2", cat_side="CRYPTO_LONG", budget_cpu_s=12, **extra)

    def test_mandatory_retained_and_overflow_exposed(self):
        result = self.plan([candidate(str(i), mandatory=True) for i in range(5)])
        self.assertTrue(all(d["selected"] for d in result["decisions"]))
        self.assertEqual(result["mandatory_budget_overflow_cpu_s"], 3)

    def test_protected_evidence_retained_even_over_budget(self):
        result = self.plan([dict(candidate("E"), eval_cpu_s=100, protected_evidence=True)])
        self.assertEqual(result["decisions"][0]["reason"], "KEEP_PROTECTED_EVIDENCE")
        self.assertGreater(result["mandatory_budget_overflow_cpu_s"], 0)

    def test_scope_mismatch_refused(self):
        with self.assertRaises(ValueError):
            self.plan([dict(candidate("X"), cat_side="STOCKS_LONG")])
        key = observation_key("engine1:wiring2", "STOCKS_LONG", "switch", "X")
        self.assertTrue(self.plan([candidate("X")], {key: 500})["decisions"][0]["first_observation"])

    def test_floor_carry_is_not_an_inclusion_guarantee(self):
        result = self.plan([dict(candidate("X"), eval_cpu_s=100)], floor_every=1)
        self.assertEqual(result["decisions"][0]["reason"], "CARRY_EXPLORATION_FLOOR_BUDGET")
        self.assertIsNone(result["guaranteed_inclusion_probability"])

    def test_cost_counts_joint_and_fixed_overhead(self):
        result = self.plan([candidate(str(i)) for i in range(10)])
        self.assertEqual(result["estimated_cpu_s"], 12)
        self.assertEqual(sum(d["selected"] for d in result["decisions"]), 4)

    def test_new_switch_ignores_old_era_zero_history_and_pos_sym(self):
        observations = {observation_key("old", "CRYPTO_LONG", "switch", "X"): 10000}
        result = self.plan([candidate("X", pos_sym=0)], observations)
        self.assertTrue(result["decisions"][0]["first_observation"])

    def test_pair_and_switch_observation_counters_are_separate(self):
        items = [candidate("X"), dict(candidate("pair"), switch="X", pair="F=ON")]
        seen = {observation_key("engine1:wiring2", "CRYPTO_LONG", "switch", "X"): 1}
        decisions = self.plan(items, seen)["decisions"]
        self.assertFalse(decisions[0]["first_observation"])
        self.assertTrue(decisions[1]["first_observation"])

    def test_first_observation_reserve_beats_large_prediction(self):
        items = [dict(candidate("new"), predicted_marginal_delta=0), dict(candidate("known"), predicted_marginal_delta=1000)]
        seen = {observation_key("engine1:wiring2", "CRYPTO_LONG", "switch", "known"): 1}
        result = self.plan(items, seen, first_share=0.5)
        self.assertEqual(result["decisions"][0]["reason"], "KEEP_FIRST_OBSERVATION")

    def test_budget_carries_truthfully_without_numeric_delta(self):
        items = [candidate(str(i)) for i in range(50)]
        result = self.plan(items)
        carried = [d for d in result["decisions"] if not d["selected"]]
        self.assertTrue(carried)
        self.assertTrue(all(d["delta"] is None and d["queue_age"] == 1 for d in carried))

    def test_aged_first_observation_receives_reserved_slot(self):
        items = [candidate("young"), candidate("aged", queue_age=100)]
        decisions = self.plan(items, first_share=0.5)["decisions"]
        self.assertEqual(decisions[1]["reason"], "KEEP_FIRST_OBSERVATION")

    def test_reserved_quota_can_fit_one_indivisible_bundle(self):
        decisions = self.plan([candidate("new")])["decisions"]
        self.assertEqual(decisions[0]["reason"], "KEEP_FIRST_OBSERVATION")

    def test_first_observation_queue_eventually_rotates(self):
        items = [candidate(str(i)) for i in range(12)]
        seen = {}
        selected = set()
        for cycle in range(12):
            result = self.plan(items, seen, cycle=cycle)
            for decision in result["decisions"]:
                item = next(x for x in items if x["id"] == decision["id"])
                item["queue_age"] = decision["queue_age"]
                if decision["selected"]:
                    selected.add(decision["id"])
                    seen[observation_key("engine1:wiring2", "CRYPTO_LONG", "switch", item["switch"])] = 1
        self.assertEqual(len(selected), 12)

    def test_deterministic_and_inputs_unchanged(self):
        item = candidate("X")
        before = dict(item)
        self.assertEqual(self.plan([item]), self.plan([item]))
        self.assertEqual(item, before)

    def test_rejects_zero_cost_nonfinite_and_impossible_floor(self):
        for item in (dict(candidate("zero"), eval_cpu_s=0, joint_cpu_s=0, fixed_cpu_s=0), dict(candidate("nan"), predicted_marginal_delta=float("nan"))):
            with self.assertRaises(ValueError):
                self.plan([item])
        with self.assertRaises(ValueError):
            self.plan([candidate("X")], floor_every=1001)


if __name__ == "__main__":
    unittest.main()
