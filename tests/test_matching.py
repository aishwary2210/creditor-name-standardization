import csv
import unittest
from pathlib import Path

from matching import (HIGH_VOLUME_ROWS, can_merge, cluster, load_aliases, resolve,
                      suggest_merges)

DATA = Path(__file__).resolve().parents[1] / "data"


def read_sample():
    with open(DATA / "sample_creditors.csv", newline="") as f:
        raw_counts = {row["raw_name"]: int(row["row_count"]) for row in csv.DictReader(f)}
    with open(DATA / "sample_aliases.csv", newline="") as f:
        lookup = load_aliases((row["alias"], row["canonical_name"]) for row in csv.DictReader(f))
    return raw_counts, lookup


def by_name(result):
    return {row["normalized_name"]: row for row in result.aliases}


class ResolveTests(unittest.TestCase):
    def setUp(self):
        self.lookup = load_aliases([("ASTER BANK", "ASTER BANK"), ("ASTER SAVINGS", "ASTER BANK")])

    def test_curated_alias_wins(self):
        aliases = by_name(resolve({"01 Aster Savings LLC": 4}, self.lookup))
        self.assertEqual(aliases["ASTER SAVINGS"]["canonical_name"], "ASTER BANK")
        self.assertEqual(aliases["ASTER SAVINGS"]["tier"], "VERIFIED")

    def test_close_spelling_is_accepted(self):
        lookup = load_aliases([("BLUE HARBOR CREDIT", "BLUE HARBOR CREDIT")])
        row = by_name(resolve({"Blue Harbour Credit": 3}, lookup))["BLUE HARBOUR CREDIT"]
        self.assertEqual(row["canonical_name"], "BLUE HARBOR CREDIT")
        self.assertEqual(row["tier"], "FUZZY")

    def test_borderline_match_waits_for_review(self):
        lookup = load_aliases([("NORTHWIND CAPITAL", "NORTHWIND CAPITAL")])
        result = resolve({"Northwind Capitol": 11}, lookup)
        row = by_name(result)["NORTHWIND CAPITOL"]
        self.assertEqual(row["canonical_name"], "NORTHWIND CAPITOL")
        self.assertEqual(row["tier"], "REVIEW")
        self.assertEqual(result.review[0]["suggested_canonical"], "NORTHWIND CAPITAL")

    def test_two_large_creditors_are_never_merged_automatically(self):
        lookup = load_aliases([("BLUE HARBOR CREDIT", "BLUE HARBOR CREDIT")])
        big = HIGH_VOLUME_ROWS
        result = resolve({"Blue Harbor Credit": big, "Blue Harbour Credit": big}, lookup)
        self.assertEqual(by_name(result)["BLUE HARBOUR CREDIT"]["tier"], "REVIEW")
        self.assertTrue(result.review[0]["high_volume"])

    def test_override_beats_everything(self):
        result = resolve({"Aster Bank Auto": 5}, self.lookup, {"ASTER BANK AUTO": "ASTER BANK"})
        row = by_name(result)["ASTER BANK AUTO"]
        self.assertEqual((row["canonical_name"], row["tier"]), ("ASTER BANK", "MANUAL"))

    def test_conflicting_aliases_are_rejected(self):
        with self.assertRaises(ValueError):
            load_aliases([("ASTER BK", "ASTER BANK"), ("ASTER BANK", "ORION FUNDING")])

    def test_sample_every_name_mapped_once_and_rows_reconcile(self):
        raw_counts, lookup = read_sample()
        result = resolve(raw_counts, lookup)
        total = sum(raw_counts.values())

        self.assertEqual(len(result.normalized), len(raw_counts))
        names = [row["normalized_name"] for row in result.aliases]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(set(names), {row["normalized_name"] for row in result.normalized})
        self.assertEqual(sum(row["source_rows"] for row in result.aliases), total)
        self.assertEqual(sum(row["source_rows"] for row in result.canonicals), total)

        ids = {row["canonical_name"]: row["canonical_id"] for row in result.canonicals}
        for row in result.aliases:
            self.assertEqual(ids[row["canonical_name"]], row["canonical_id"])

    def test_sample_outcomes(self):
        aliases = by_name(resolve(*read_sample()))
        expected = {
            "NWC": ("NORTHWIND CAPITAL", "VERIFIED"),
            "BHC CARD SERVICES": ("BLUE HARBOR CREDIT", "VERIFIED"),
            "ORION FUNDNG": ("ORION FUNDING", "CLUSTERED"),
            "RIVER BEND LENDING": ("RIVERBEND LENDING", "CLUSTERED"),
            "GREEN LAWN FERTILIZING": ("GREEN LAWN FERTILIZING", "LOW_FREQUENCY"),
            "NORTHSTAR SERVICES": ("NORTHSTAR SERVICES", "STANDALONE"),
        }
        for name, (canonical, tier) in expected.items():
            self.assertEqual((aliases[name]["canonical_name"], aliases[name]["tier"]),
                             (canonical, tier), name)


class ClusterTests(unittest.TestCase):
    def test_guards_reject_names_that_only_share_a_word(self):
        self.assertTrue(can_merge("ORION FUNDING", "ORION FUNDNG"))
        self.assertFalse(can_merge("GREEN DOT BANK", "GREEN LAWN FERTILIZING"))
        self.assertFalse(can_merge("ASTER", "ASTER BANK AUTO FINANCE"))

    def test_members_must_match_the_representative(self):
        rows = {"ORION FUNDING": 100, "ORION FUNDING GROUP": 10, "ORION FUNDING GROUP WEST": 5}
        assigned, _ = cluster(list(rows), rows)
        self.assertEqual(assigned["ORION FUNDING GROUP"][0], "ORION FUNDING")
        self.assertNotIn("ORION FUNDING GROUP WEST", assigned)


class SuggestTests(unittest.TestCase):
    def test_already_decided_pairs_are_not_asked_again(self):
        canonicals = [
            {"canonical_name": "ASTER BANK", "tier": "VERIFIED", "source_rows": 2000},
            {"canonical_name": "ASTER BANK AUTO", "tier": "STANDALONE", "source_rows": 40},
            {"canonical_name": "ASTER BANK/RETAIL", "tier": "LOW_FREQUENCY", "source_rows": 6},
        ]
        found = suggest_merges(canonicals, [], decided=[("ASTER BANK AUTO", "ASTER BANK")])
        self.assertEqual([s["current_name"] for s in found], ["ASTER BANK/RETAIL"])

    def test_rules(self):
        canonicals = [
            {"canonical_name": "ASTER BANK", "tier": "VERIFIED", "source_rows": 2000},
            {"canonical_name": "ASTER BANK AUTO", "tier": "STANDALONE", "source_rows": 40},
            {"canonical_name": "ASTER BANK/RETAIL", "tier": "LOW_FREQUENCY", "source_rows": 6},
            {"canonical_name": "ORION FUNDING", "tier": "CLUSTERED", "source_rows": 9000},
            {"canonical_name": "ORION FUNDING EAST", "tier": "STANDALONE", "source_rows": 300},
            {"canonical_name": "NORTHWIND CAPITOL", "tier": "REVIEW", "source_rows": 11},
        ]
        review = [{"normalized_name": "NORTHWIND CAPITOL", "suggested_canonical": "NORTHWIND CAPITAL"}]
        found = {s["current_name"]: (s["suggested_canonical"], s["reason"])
                 for s in suggest_merges(canonicals, review)}
        self.assertEqual(found, {
            "ASTER BANK AUTO": ("ASTER BANK", "prefix"),
            "ASTER BANK/RETAIL": ("ASTER BANK", "split"),
            "ORION FUNDING EAST": ("ORION FUNDING", "high_volume_prefix"),
            "NORTHWIND CAPITOL": ("NORTHWIND CAPITAL", "near_match"),
        })


if __name__ == "__main__":
    unittest.main()
