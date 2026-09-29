import unittest

from normalize import normalize_company


class NormalizeTests(unittest.TestCase):
    def check(self, raw, expected):
        self.assertEqual(normalize_company(raw), expected, raw)

    def test_bureau_prefix_and_abbreviations(self):
        self.check("08 CAP1 BK NA", "CAPITAL ONE BANK")
        self.check("First Natl Bk of Omaha", "FIRST NATIONAL BANK OF OMAHA")
        self.check("SYNCB/AMAZON", "SYNCHRONY BANK/AMAZON")

    def test_legal_suffixes(self):
        self.check("Blue Harbor Credit, Inc.", "BLUE HARBOR CREDIT")
        self.check("Aster Bank N.A.", "ASTER BANK")
        self.check("07 Orion Funding LLC", "ORION FUNDING")

    def test_dba_keeps_operating_name(self):
        self.check("Riverstone Collections DBA NWC", "NWC")

    def test_account_type_codes_and_installment(self):
        self.check("Riverbend Lending [IA]", "RIVERBEND LENDING")
        self.check("Cedar Fin Installment Loan", "CEDAR FINANCIAL")

    def test_messy_characters(self):
        self.check("Café  Crédit Union", "CAFE CREDIT UNION")
        self.check("CAPITAL ONE\\r", "CAPITAL ONE")
        self.check("CAPITAL ONE\r\n", "CAPITAL ONE")

    def test_blank_values(self):
        self.check(None, "")
        self.check("   ", "")


if __name__ == "__main__":
    unittest.main()
