import unittest
from pathlib import Path

from creditor_standardization import can_cluster, normalize, read_aliases, read_names, resolve


class ResolverTests(unittest.TestCase):
    def test_normalization_and_exact_alias(self):
        self.assertEqual(normalize("01 Aster BK LLC"), "ASTER BANK")
        aliases, canonicals, review = resolve(
            [{"raw_name": "01 Aster BK LLC", "row_count": 3}],
            {"ASTER BANK": "ASTER BANK"},
        )
        self.assertEqual(aliases[0]["canonical_name"], "ASTER BANK")
        self.assertEqual(aliases[0]["tier"], "VERIFIED")
        self.assertEqual(canonicals[0]["source_rows"], 3)
        self.assertEqual(review, [])

    def test_every_valid_input_name_has_one_mapping_and_counts_reconcile(self):
        names = [
            {"raw_name": "Orion Funding", "row_count": 7},
            {"raw_name": "Orion Fundng", "row_count": 1},
            {"raw_name": "Northstar Services", "row_count": 2},
        ]
        aliases, canonicals, _ = resolve(names, {})
        self.assertEqual(len(aliases), 3)
        self.assertEqual(sum(row["source_rows"] for row in aliases), 10)
        self.assertEqual(sum(row["source_rows"] for row in canonicals), 10)

    def test_review_candidate_keeps_own_canonical_until_approved(self):
        names = [{"raw_name": "ASTER BANC", "row_count": 2}]
        aliases, _, review = resolve(names, {"ASTER BANK": "ASTER BANK"})
        self.assertEqual(aliases[0]["canonical_name"], "ASTER BANC")
        self.assertEqual(aliases[0]["tier"], "REVIEW")
        self.assertEqual(review[0]["suggested_match"], "ASTER BANK")

    def test_sample_exercises_review_and_direct_clustering(self):
        root = Path(__file__).resolve().parents[1]
        names = read_names(root / "data/synthetic_creditors.csv")
        seeds = read_aliases(root / "data/synthetic_aliases.csv")
        aliases, canonicals, review = resolve(names, seeds)
        self.assertEqual(len(aliases), 14)
        self.assertEqual(len(review), 1)
        self.assertEqual(sum(row["source_rows"] for row in aliases), 58)
        self.assertEqual(sum(row["source_rows"] for row in canonicals), 58)
        self.assertTrue(can_cluster("ORION FUNDING", "ORION FUNDNG"))
        self.assertFalse(can_cluster("GREEN DOT BANK", "GREEN LAWN FERTILIZING"))


if __name__ == "__main__":
    unittest.main()
