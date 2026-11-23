"""Payments Canada 80-byte (format TD) EFT records: A, X, C/D, Y and Z."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
import re
from typing import Any

from . import CadeftError, ParseError, ValidationError, eft_date, parse_date

RECORD_LENGTH = 80
# "/", "(" and ")" are not in the spec table but appear in the TD sample.
_ALLOWED = re.compile(r"[0-9A-Z =$.*,/()\-]*")


def _num(value: str, what: str) -> int:
    if not re.fullmatch(r"[0-9]+", value):
        raise ParseError(f"{what} must be numeric: {value!r}")
    return int(value)


def _fixed(value: int, width: int, what: str) -> str:
    if value < 0 or len(str(value)) > width:
        raise ValidationError(f"{what} {value} does not fit in {width} digits")
    return str(value).zfill(width)


def _text(value: str, width: int, what: str) -> str:
    if len(value) > width:
        raise ValidationError(f"{what} exceeds {width} characters")
    return value.ljust(width)


def _pad(line: str, record_type: str) -> str:
    line = line.rstrip()
    if not line.startswith(record_type):
        raise ParseError(f"expected record type {record_type!r}")
    if len(line) > RECORD_LENGTH:
        raise ParseError(f"record is {len(line)} characters, maximum is {RECORD_LENGTH}")
    return line.ljust(RECORD_LENGTH)


def _check_chars(*values: str) -> None:
    for value in values:
        if not _ALLOWED.fullmatch(value):
            raise ValidationError(f"invalid characters in {value!r}")


def _date(value: str) -> date:
    try:
        return parse_date(value)
    except ValueError as exc:
        raise ParseError(str(exc)) from exc


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


@dataclass
class Header80:
    originator_id: str = ""
    file_creation_number: int = 0
    creation_date: date | datetime | None = None
    destination_data_centre: int = 0

    record_type = "A"

    def build(self) -> str:
        return (
            "A" + _text(self.originator_id, 10, "originator id")
            + _fixed(self.file_creation_number, 4, "file creation number")
            + eft_date(self.creation_date)
            + _fixed(self.destination_data_centre, 5, "destination data centre")
        ).ljust(RECORD_LENGTH)

    @classmethod
    def parse(cls, line: str) -> Header80:
        line = _pad(line, "A")
        return cls(
            originator_id=line[1:11].rstrip(),
            file_creation_number=_num(line[11:15], "file creation number"),
            creation_date=_date(line[15:21]),
            destination_data_centre=_num(line[21:26], "destination data centre"),
        )

    def validate(self) -> None:
        if not self.originator_id.strip():
            raise ValidationError("originator id is required")
        if self.creation_date is None:
            raise ValidationError("file creation date is required")
        _check_chars(self.originator_id)
        self.build()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordType": "A",
            "originatorId": self.originator_id,
            "fileCreationNumber": self.file_creation_number,
            "creationDate": self.creation_date.isoformat() if self.creation_date else None,
            "destinationDataCentre": self.destination_data_centre,
        }


@dataclass
class BatchHeader80:
    payment_type: str = "C"
    transaction_type: str = ""
    due_date: date | datetime | None = None
    short_name: str = ""
    long_name: str = ""
    return_institution_id: str = ""
    return_account_no: str = ""

    record_type = "X"

    def build(self) -> str:
        return (
            "X" + self.payment_type
            + _text(self.transaction_type, 3, "transaction type")
            + eft_date(self.due_date)
            + _text(self.short_name, 15, "short name")
            + _text(self.long_name, 30, "long name")
            + _text(self.return_institution_id, 9, "return institution id")
            + _text(self.return_account_no, 12, "return account number")
        ).ljust(RECORD_LENGTH)

    @classmethod
    def parse(cls, line: str) -> BatchHeader80:
        line = _pad(line, "X")
        return cls(
            payment_type=line[1],
            transaction_type=line[2:5],
            due_date=_date(line[5:11]),
            short_name=line[11:26].rstrip(),
            long_name=line[26:56].rstrip(),
            return_institution_id=line[56:65].rstrip(),
            return_account_no=line[65:77].rstrip(),
        )

    def validate(self) -> None:
        if self.payment_type not in ("C", "D"):
            raise ValidationError("batch payment type must be C or D")
        if not re.fullmatch(r"[0-9]{3}", self.transaction_type):
            raise ValidationError("transaction type must be 3 digits")
        if self.due_date is None:
            raise ValidationError("due date is required")
        if not re.fullmatch(r"0[0-9]{8}", self.return_institution_id):
            raise ValidationError("return institution id must be 0BBBTTTTT")
        _check_chars(self.short_name, self.long_name, self.return_account_no)
        self.build()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordType": "X",
            "paymentType": self.payment_type,
            "transactionType": self.transaction_type,
            "dueDate": self.due_date.isoformat() if self.due_date else None,
            "shortName": self.short_name,
            "longName": self.long_name,
            "returnInstitutionId": self.return_institution_id,
            "returnAccountNo": self.return_account_no,
        }


@dataclass
class Detail80:
    record_type: str = "C"
    amount: int = 0
    institution_id: str = ""
    account_no: str = ""
    name: str = ""
    cross_reference: str = ""

    def build(self) -> str:
        width = 15 if self.record_type == "D" else 19
        return (
            self.record_type + _fixed(self.amount, 10, "amount")
            + _text(self.institution_id, 9, "institution id")
            + _text(self.account_no, 12, "account number")
            + _text(self.name, 29, "payee/payor name")
            + _text(self.cross_reference, width, "cross reference")
        ).ljust(RECORD_LENGTH)

    @classmethod
    def parse(cls, line: str) -> Detail80:
        record_type = line[:1]
        if record_type not in ("C", "D"):
            raise ParseError(f"invalid detail record type {record_type!r}")
        line = _pad(line, record_type)
        return cls(
            record_type=record_type,
            amount=_num(line[1:11], "amount"),
            institution_id=line[11:20].rstrip(),
            account_no=line[20:32].rstrip(),
            name=line[32:61].rstrip(),
            cross_reference=line[61:80].rstrip(),
        )

    def validate(self) -> None:
        if self.record_type not in ("C", "D"):
            raise ValidationError("detail record type must be C or D")
        if not re.fullmatch(r"0[0-9]{8}", self.institution_id):
            raise ValidationError("institution id must be 0BBBTTTTT")
        if not self.account_no.strip() or " " in self.account_no.strip():
            raise ValidationError("account number is required and must not contain blanks")
        _check_chars(self.account_no, self.name, self.cross_reference)
        self.build()

    def to_dict(self) -> dict[str, Any]:
        return {
            "recordType": self.record_type,
            "amount": self.amount,
            "institutionId": self.institution_id,
            "accountNo": self.account_no,
            "name": self.name,
            "crossReference": self.cross_reference,
        }


@dataclass
class BatchControl80:
    payment_type: str = "C"
    record_count: int = 0
    amount: int = 0

    record_type = "Y"

    def build(self) -> str:
        return (
            "Y" + self.payment_type + _fixed(self.record_count, 8, "record count")
            + _fixed(self.amount, 14, "batch amount")
        ).ljust(RECORD_LENGTH)

    @classmethod
    def parse(cls, line: str) -> BatchControl80:
        line = _pad(line, "Y")
        return cls(line[1], _num(line[2:10], "record count"), _num(line[10:24], "batch amount"))

    def to_dict(self) -> dict[str, Any]:
        return {"recordType": "Y", "paymentType": self.payment_type,
                "recordCount": self.record_count, "amount": self.amount}


@dataclass
class Footer80:
    debit_amount: int = 0
    debit_count: int = 0
    credit_amount: int = 0
    credit_count: int = 0

    record_type = "Z"

    def build(self) -> str:
        return (
            "Z" + _fixed(self.debit_amount, 14, "debit total")
            + _fixed(self.debit_count, 5, "debit count")
            + _fixed(self.credit_amount, 14, "credit total")
            + _fixed(self.credit_count, 5, "credit count")
        ).ljust(RECORD_LENGTH)

    @classmethod
    def parse(cls, line: str) -> Footer80:
        line = _pad(line, "Z")
        return cls(
            _num(line[1:15], "debit total"), _num(line[15:20], "debit count"),
            _num(line[20:34], "credit total"), _num(line[34:39], "credit count"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"recordType": "Z", "debitAmount": self.debit_amount, "debitCount": self.debit_count,
                "creditAmount": self.credit_amount, "creditCount": self.credit_count}


@dataclass
class Batch80:
    header: BatchHeader80
    details: list[Detail80] = field(default_factory=list)
    control: BatchControl80 | None = None

    def computed_control(self) -> BatchControl80:
        return BatchControl80(self.header.payment_type, len(self.details),
                              sum(d.amount for d in self.details))

    def to_dict(self) -> dict[str, Any]:
        return {"header": self.header.to_dict(), "details": [d.to_dict() for d in self.details],
                "control": (self.control or self.computed_control()).to_dict()}


@dataclass
class File80:
    header: Header80
    batches: list[Batch80] = field(default_factory=list)
    footer: Footer80 | None = None

    def computed_footer(self) -> Footer80:
        footer = Footer80()
        for batch in self.batches:
            for d in batch.details:
                if d.record_type == "D":
                    footer.debit_amount += d.amount
                    footer.debit_count += 1
                else:
                    footer.credit_amount += d.amount
                    footer.credit_count += 1
        return footer

    def create(self, eol: str = "\n") -> str:
        """Build the file; batch controls and footer are computed from the details."""
        lines = [self.header.build()]
        for batch in self.batches:
            lines.append(batch.header.build())
            lines.extend(d.build() for d in batch.details)
            lines.append(batch.computed_control().build())
        lines.append(self.computed_footer().build())
        return eol.join(lines) + eol

    def validate(self) -> None:
        self.header.validate()
        creation = _as_date(self.header.creation_date)
        for batch in self.batches:
            batch.header.validate()
            due = _as_date(batch.header.due_date)
            if (due - creation).days > 100:
                raise ValidationError("due date must not exceed 100 days from file creation date")
            for detail in batch.details:
                detail.validate()
                if detail.record_type != batch.header.payment_type:
                    raise ValidationError(
                        f"{detail.record_type} record in a {batch.header.payment_type} batch")
            if batch.control is not None and batch.control != batch.computed_control():
                raise ValidationError("batch control does not match batch details")
        if self.footer is not None and self.footer != self.computed_footer():
            raise ValidationError("file footer does not match batch details")

    def to_dict(self) -> dict[str, Any]:
        return {"header": self.header.to_dict(), "batches": [b.to_dict() for b in self.batches],
                "footer": (self.footer or self.computed_footer()).to_dict()}


def parse_file80(text: str) -> File80:
    """Parse an 80-byte file delimited by CR, LF or CRLF; trailing blanks may be trimmed."""
    lines = [line for line in re.split(r"\r\n|\r|\n", text) if line.strip()]
    if not lines or lines[0][:1] != "A":
        raise ParseError("file must start with an A record")
    file = File80(Header80.parse(lines[0]))
    batch: Batch80 | None = None
    for number, line in enumerate(lines[1:], start=2):
        kind = line[0]
        try:
            if kind == "X":
                if batch is not None:
                    raise ParseError("batch header before previous batch control")
                batch = Batch80(BatchHeader80.parse(line))
            elif kind in ("C", "D"):
                if batch is None:
                    raise ParseError("detail record outside a batch")
                batch.details.append(Detail80.parse(line))
            elif kind == "Y":
                if batch is None:
                    raise ParseError("batch control without a batch header")
                batch.control = BatchControl80.parse(line)
                file.batches.append(batch)
                batch = None
            elif kind == "Z":
                if batch is not None:
                    raise ParseError("file footer before batch control")
                file.footer = Footer80.parse(line)
                if number != len(lines):
                    raise ParseError("file footer must be the last record")
            else:
                raise ParseError(f"unknown record type {kind!r}")
        except CadeftError as exc:
            raise type(exc)(f"line {number}: {exc}") from exc
    if batch is not None:
        raise ParseError("missing batch control record")
    if file.footer is None:
        raise ParseError("missing file footer record")
    return file
