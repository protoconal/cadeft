import unittest

from cadeft import ParseError, ValidationError
from cadeft.eft80 import RECORD_LENGTH, Detail80, parse_file80

SAMPLE = "\r\n".join([
    "ACOMPANY12C000002036600120",
    "XC430020366SHORT NAME15CARLONG NAME 30 CHARACTERS       0001000111234567        ",
    "C00000012340001000115678901     PAYEE PAYOR NAME 29 CHARS    3021                           ",
    "YC0000000100000000001234",
    "Z00000000000000000000000000000123400001",
])


class Eft80Tests(unittest.TestCase):
    def test_rejects_non_spec_characters(self):
        bad = SAMPLE.replace("PAYEE PAYOR", "PAYEE/PAYOR")
        with self.assertRaises(ParseError):
            parse_file80(bad)

    def test_create_defaults_to_crlf(self):
        out = parse_file80(SAMPLE).create()
        self.assertTrue(out.endswith("\r\n"))
        self.assertEqual(out.count("\r\n"), out.count("\n"))

    def test_parse_sample(self):
        eft = parse_file80(SAMPLE)
        eft.validate()
        self.assertEqual(eft.header.originator_id, "COMPANY12C")
        batch = eft.batches[0]
        self.assertEqual(batch.header.short_name, "SHORT NAME15CAR")
        self.assertEqual(batch.header.return_account_no, "1234567")
        detail = batch.details[0]
        self.assertEqual((detail.amount, detail.institution_id, detail.account_no),
                         (1234, "000100011", "5678901"))
        self.assertEqual(detail.name, "PAYEE PAYOR NAME 29 CHARS")
        self.assertEqual(detail.cross_reference, "3021")
        self.assertEqual(eft.footer.credit_amount, 1234)

    def test_create_round_trip_is_80_wide(self):
        eft = parse_file80(SAMPLE)
        out = eft.create()
        lines = out.split("\r\n")[:-1]
        self.assertTrue(all(len(line) == RECORD_LENGTH for line in lines))
        self.assertEqual([l.rstrip() for l in lines], [l.rstrip() for l in SAMPLE.split("\r\n")])
        self.assertEqual(parse_file80(out).to_dict(), eft.to_dict())

    def test_only_crlf_accepted(self):
        for eol in ("\n", "\r"):
            with self.assertRaises(ParseError):
                parse_file80(SAMPLE.replace("\r\n", eol))
        with self.assertRaises(ParseError):
            parse_file80(SAMPLE.replace("\r\nZ", "\nZ"))

    def test_bad_footer(self):
        eft = parse_file80(SAMPLE.replace("00001", "00002", 1)[:-5] + "00002")
        with self.assertRaises(ValidationError):
            eft.validate()

    def test_errors(self):
        with self.assertRaises(ParseError):
            parse_file80(SAMPLE.replace("\r\nZ00000000000000000000000000000123400001", ""))
        with self.assertRaises(ParseError):
            parse_file80("Y")
        with self.assertRaises(ValidationError):
            Detail80("D", 1, "000100011", "123", "N", "X" * 16).build()


if __name__ == "__main__":
    unittest.main()
