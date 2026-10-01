from datetime import date
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

import cadeft


ROOT = Path(__file__).resolve().parents[1]
TEST_DATE = date(2023, 10, 2)


def transactions():
    common = ("450", 1234, TEST_DATE, "123456789", "123456789012",
              "1234567890123456789012", "ORIGINATOR", "RECIPIENT",
              "LONG ORIGINATOR")
    return [
        cadeft.NewDebit(*common, "123456789", "123456789012"),
        cadeft.NewCredit(*common, "123456789", "123456789012"),
        cadeft.NewDebitReturn(*common, "123456789", "123456789012", "2222222222222222222222"),
        cadeft.NewCreditReturn(*common, "123456789", "123456789012", "2222222222222222222222"),
        cadeft.NewCreditReverse(*common, "123456789", "123456789012", "2222222222222222222222"),
        cadeft.NewDebitReverse(*common, "123456789", "123456789012", "2222222222222222222222"),
    ]


class UtilityTests(unittest.TestCase):
    def test_dates_numbers_and_unicode(self):
        self.assertEqual(cadeft.parse_date("023001"), date(2023, 1, 1))
        self.assertEqual(cadeft.parse_date("023000"), date(2022, 12, 31))
        self.assertEqual(cadeft.parse_date("023366"), date(2024, 1, 1))
        self.assertEqual(cadeft.parse_num("00123"), 123)
        self.assertEqual(cadeft.normalize("E\u0301"), "É")
        with self.assertRaises(ValueError):
            cadeft.parse_num("12x")
        with self.assertRaises(ValueError):
            cadeft.parse_date("12345")


class TransactionTests(unittest.TestCase):
    def test_each_record_build_parse_and_validation(self):
        for txn in transactions():
            with self.subTest(record_type=txn.get_type()):
                segment = txn.build()
                self.assertEqual(len(segment), cadeft.SEGMENT_LENGTH)
                parsed = type(txn)().parse(segment)
                self.assertEqual(parsed.get_type(), txn.get_type())
                self.assertEqual(parsed.amount, txn.amount)
                self.assertEqual(parsed.date_value, txn.date_value)
                self.assertEqual(parsed.get_account_no(), txn.get_account_no())
                self.assertEqual(parsed.get_name(), txn.get_name())
                self.assertEqual(parsed.get_base_txn().record_type, txn.get_type())
                parsed.validate()

    def test_unicode_name_is_normalized_and_preserved(self):
        txn = cadeft.NewCredit(
            "450", 100, TEST_DATE, "123456789", "123", "1",
            "Nom E\u0301metteur", "Payee", "Originator", "123456789", "123",
        )
        parsed = cadeft.Credit().parse(txn.build())
        self.assertEqual(parsed.short_name, "Nom Émetteur")

    def test_constructor_options(self):
        txn = cadeft.new_credit(
            "450", 100, TEST_DATE, "123456789", "123", "1",
            "Originator", "Payee", "Originator long name", "123456789", "123",
            cadeft.WithUserID("00123"),
            cadeft.WithCrossRefNo("0000123"),
        )
        self.assertEqual(txn.user_id, "00123")
        self.assertEqual(txn.cross_ref_no, "0000123")
        header = cadeft.NewFileHeader(
            "0000000001", 1, TEST_DATE, 610, "CAD",
            cadeft.WithDirectClearerCommunicationArea("clearing"),
        )
        self.assertEqual(header.communication_area, "clearing")

    def test_parse_errors_and_validation_errors(self):
        with self.assertRaises(cadeft.ParseError):
            cadeft.Debit().parse("short")
        txn = cadeft.Debit()
        with self.assertRaises(cadeft.ValidationError):
            txn.validate()


class FileTests(unittest.TestCase):
    def test_cli_build_reads_json_from_stdin(self):
        eft_file = cadeft.NewFile(
            cadeft.NewFileHeader("0000000001", 1, TEST_DATE, 610, "CAD"),
            transactions()[:1],
        )
        result = subprocess.run(
            [sys.executable, "-m", "cadeft", "--mode", "build"],
            input=json.dumps(eft_file.to_dict()),
            text=True,
            capture_output=True,
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT)},
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(cadeft.Reader(result.stdout).read_file().txns), 1)

    def test_header_footer_file_roundtrip_and_filters(self):
        header = cadeft.NewFileHeader("0000000001", 1, TEST_DATE, 610, "CAD")
        txns = transactions()
        file = cadeft.NewFile(header, txns)
        text = file.create()
        lines = text.splitlines()
        self.assertEqual([len(line) for line in lines], [1464] * len(lines))
        parsed = cadeft.Reader(text).read_file()
        self.assertEqual(len(parsed.txns), len(txns))
        self.assertEqual(parsed.footer.total_count_debit, 2)
        self.assertEqual(parsed.footer.total_count_credit, 2)
        self.assertEqual(parsed.footer.total_count_reverse_debit, 1)
        self.assertEqual(parsed.footer.total_count_reverse_credit, 1)
        self.assertEqual(len(parsed.get_all_debit_txns()), 1)
        self.assertEqual(len(parsed.get_all_credits()), 1)
        self.assertEqual(len(parsed.get_all_debit_returns()), 1)
        self.assertEqual(len(parsed.get_all_credit_returns()), 1)
        parsed.validate()

    def test_streamer_reads_multiple_records_and_ends_at_footer(self):
        file = cadeft.NewFile(cadeft.NewFileHeader("0000000001", 1, TEST_DATE, 610, "CAD"), transactions())
        streamer = cadeft.FileStreamer(file.create())
        self.assertEqual(streamer.get_header().currency_code, "CAD")
        self.assertEqual(streamer.get_footer().total_count_debit, 2)
        got = []
        while True:
            try:
                got.append(streamer.scan_txn())
            except cadeft.EndOfFile:
                break
        self.assertEqual(len(got), 6)

    def test_invalid_footer_totals_are_rejected(self):
        file = cadeft.NewFile(cadeft.NewFileHeader("0000000001", 1, TEST_DATE, 610, "CAD"), transactions()[:1])
        file.footer = cadeft.FileFooter(total_value_debit=0)
        with self.assertRaises(cadeft.ValidationError):
            file.validate()

    def test_repository_sample_parses(self):
        sample = (ROOT / "sample_files" / "CO14821.txt").read_text(encoding="utf-8")
        parsed = cadeft.Reader(sample).read_file()
        self.assertEqual(parsed.header.originator_id, "0000000610")
        self.assertEqual(len(parsed.txns), 11)
        self.assertEqual(parsed.footer.total_count_credit, 6)
        self.assertEqual(parsed.footer.total_count_debit, 5)


if __name__ == "__main__":
    unittest.main()
