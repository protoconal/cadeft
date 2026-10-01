#!/usr/bin/env python3
"""Compare the Python port with the Go CLI using only standard-library tooling."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
PYTHON = [sys.executable, "-m", "cadeft"]
GO = ["go", "run", "./cmd"]
ENV = {**os.environ, "PYTHONPATH": str(ROOT)}

HEADER = {
    "type": "A",
    "originator_id": "0000000001",
    "file_creation_number": 1,
    "creation_date": "2023-10-02T00:00:00Z",
    "destination_data_center": 610,
    "communication_area": "",
    "currency_code": "CAD",
}
COMMON = {
    "txn_type": "450",
    "amount": 1234,
    "item_trace_no": "1234567890123456789012",
    "institution_id": "123456789",
    "stored_txn_type": "000",
    "short_name": "BANQUE DE TEST",
    "long_name": "EXAMPLE ORIGINATOR",
    "user_id": "0000000001",
    "cross_ref_no": "000000000000001",
    "sundry_info": "",
    "settlement_code": "01",
    "invalid_data_element_id": "",
}
TXNS = {
    "C": {
        **COMMON, "type": "C", "date_funds_available": HEADER["creation_date"],
        "payee_account_no": "123456789012", "payee_name": "PAYEE",
        "return_institution_id": "123456789", "return_account_no": "123456789012",
    },
    "D": {
        **COMMON, "type": "D", "due_date": HEADER["creation_date"],
        "payor_account_no": "123456789012", "payor_name": "PAYOR",
        "return_institution_id": "123456789", "return_account_no": "123456789012",
    },
    "E": {
        **COMMON, "type": "E", "date_funds_available": HEADER["creation_date"],
        "payee_account_no": "123456789012", "payee_name": "PAYEE",
        "return_institution_id": "123456789", "return_account_no": "123456789012",
        "original_item_trace_no": "2222222222222222222222",
    },
    "F": {
        **COMMON, "type": "F", "due_date": HEADER["creation_date"],
        "payor_account_no": "123456789012", "payor_name": "PAYOR",
        "return_institution_id": "123456789", "return_account_no": "123456789012",
        "original_item_trace_no": "2222222222222222222222",
    },
    "I": {
        **COMMON, "type": "I", "date_funds_available": HEADER["creation_date"],
        "payee_account_no": "123456789012", "payee_name": "PAYEE",
        "original_institution_id": "123456789", "original_account_no": "123456789012",
        "original_item_trace_no": "2222222222222222222222",
    },
    "J": {
        **COMMON, "type": "J", "due_date": HEADER["creation_date"],
        "payor_account_no": "123456789012", "payor_name": "PAYOR",
        "original_institution_id": "123456789", "original_account_no": "123456789012",
        "original_item_trace_no": "2222222222222222222222",
    },
}


def run(command: list[str], *, file_path: Path | None = None, data: str | None = None) -> subprocess.CompletedProcess[str]:
    args = command + (["--file", str(file_path)] if file_path else [])
    return subprocess.run(args, input=data, text=True, capture_output=True, cwd=ROOT, env=ENV, check=False)


def require_same_status(go_result: subprocess.CompletedProcess[str], py_result: subprocess.CompletedProcess[str], label: str) -> None:
    if (go_result.returncode == 0) != (py_result.returncode == 0):
        raise AssertionError(
            f"{label}: Go status={go_result.returncode} stderr={go_result.stderr!r}; "
            f"Python status={py_result.returncode} stderr={py_result.stderr!r}"
        )


def main() -> int:
    sample = ROOT / "sample_files" / "CO14821.txt"
    with tempfile.TemporaryDirectory(prefix="cadeft-parity-") as temp:
        temp_dir = Path(temp)

        go_parse = run(GO + ["--mode", "parse"], file_path=sample)
        py_parse = run(PYTHON + ["--mode", "parse"], file_path=sample)
        require_same_status(go_parse, py_parse, "sample file parse")
        if go_parse.returncode:
            raise RuntimeError(go_parse.stderr)
        if json.loads(go_parse.stdout) != json.loads(py_parse.stdout):
            raise AssertionError("sample file parse produced different JSON values")
        print("PASS: sample file parse output")

        for record_type, txn in TXNS.items():
            payload = {"file_header": HEADER, "transactions": [txn]}
            input_file = temp_dir / f"{record_type}.json"
            input_file.write_text(json.dumps(payload), encoding="utf-8")
            go_build = run(GO + ["--mode", "build"], file_path=input_file)
            py_build = run(PYTHON + ["--mode", "build"], file_path=input_file)
            require_same_status(go_build, py_build, f"{record_type} build")
            if go_build.returncode:
                raise RuntimeError(f"{record_type} build failed: {go_build.stderr}")
            if go_build.stdout != py_build.stdout:
                raise AssertionError(f"{record_type} serialized output differs")
            eft_file = temp_dir / f"{record_type}.eft"
            eft_file.write_text(go_build.stdout, encoding="utf-8")
            go_roundtrip = run(GO + ["--mode", "parse"], file_path=eft_file)
            py_roundtrip = run(PYTHON + ["--mode", "parse"], file_path=eft_file)
            require_same_status(go_roundtrip, py_roundtrip, f"{record_type} parse")
            if json.loads(go_roundtrip.stdout) != json.loads(py_roundtrip.stdout):
                raise AssertionError(f"{record_type} parse produced different JSON values")
            print(f"PASS: {record_type} build and parse")

        valid_input = temp_dir / "valid.json"
        valid_input.write_text(json.dumps({"file_header": HEADER, "transactions": [TXNS["D"]]}), encoding="utf-8")
        go_valid = run(GO + ["--mode", "build", "--validate"], file_path=valid_input)
        py_valid = run(PYTHON + ["--mode", "build", "--validate"], file_path=valid_input)
        require_same_status(go_valid, py_valid, "valid file validation")
        if go_valid.returncode:
            raise AssertionError(f"valid file rejected: Go={go_valid.stderr} Python={py_valid.stderr}")

        invalid_payload = {"file_header": {**HEADER, "currency_code": "ZZZ"}, "transactions": [TXNS["D"]]}
        invalid_input = temp_dir / "invalid.json"
        invalid_input.write_text(json.dumps(invalid_payload), encoding="utf-8")
        go_invalid = run(GO + ["--mode", "build", "--validate"], file_path=invalid_input)
        py_invalid = run(PYTHON + ["--mode", "build", "--validate"], file_path=invalid_input)
        require_same_status(go_invalid, py_invalid, "invalid file validation")
        if go_invalid.returncode == 0:
            raise AssertionError("both implementations accepted an invalid currency code")
        print("PASS: validation success and failure outcomes")

        invalid_eft = temp_dir / "invalid.eft"
        invalid_eft.write_text(HEADER["type"] + "00000000100000000010001" + "02327500610" + " " * 1429 + "\nD123\n", encoding="utf-8")
        go_error = run(GO + ["--mode", "parse"], file_path=invalid_eft)
        py_error = run(PYTHON + ["--mode", "parse"], file_path=invalid_eft)
        require_same_status(go_error, py_error, "malformed input")
        if go_error.returncode == 0:
            raise AssertionError("both implementations accepted a malformed transaction")
        print("PASS: malformed input failure outcome")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
