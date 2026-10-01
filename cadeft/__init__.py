"""Python implementation of Payments Canada EFT Standard 005 files."""

from __future__ import annotations

from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
import re
import unicodedata
from typing import Any, BinaryIO, Callable, TextIO

HEADER_RECORD = "A"
CREDIT_RECORD = "C"
DEBIT_RECORD = "D"
CREDIT_REVERSE_RECORD = "E"
DEBIT_REVERSE_RECORD = "F"
RETURN_CREDIT_RECORD = "I"
RETURN_DEBIT_RECORD = "J"
DEBIT_RETURN_RECORD = RETURN_DEBIT_RECORD
CREDIT_RETURN_RECORD = RETURN_CREDIT_RECORD
NOTICE_OF_CHANGE_RECORD = "S"
NOTICE_OF_CHANGE_HEADER = "U"
NOTICE_OF_CHANGE_FOOTER = "V"
FOOTER_RECORD = "Z"

MAX_LINE_LENGTH = 1464
COMMON_RECORD_DATA_LENGTH = 24
SEGMENT_LENGTH = 240
MAX_TXNS_PER_RECORD = 6
RecordType = str
TransactionType = str
DCSign = str
Transactions = list["Transaction"]
BaseTxnOpt = Callable[[dict[str, str]], None]
HeaderOpts = dict[str, str]

_RECORD_TYPES = {
    DEBIT_RECORD,
    CREDIT_RECORD,
    CREDIT_REVERSE_RECORD,
    DEBIT_REVERSE_RECORD,
    RETURN_CREDIT_RECORD,
    RETURN_DEBIT_RECORD,
}
_VALID_RECORD_TYPES = _RECORD_TYPES | {
    HEADER_RECORD,
    NOTICE_OF_CHANGE_RECORD,
    NOTICE_OF_CHANGE_HEADER,
    NOTICE_OF_CHANGE_FOOTER,
}


class CadeftError(ValueError):
    """Base exception for parsing, validation, and serialization errors."""


class ParseError(CadeftError):
    """An EFT record could not be parsed."""


class ValidationError(CadeftError):
    """One or more EFT fields failed validation."""


class EndOfFile(CadeftError):
    """The transaction stream has no more records."""


def normalize(value: str) -> str:
    return unicodedata.normalize("NFC", value)


def parse_num(value: str) -> int:
    if not re.fullmatch(r"[+-]?[0-9]+", value):
        raise ValueError(f'invalid syntax for integer: "{value}"')
    result = int(value, 10)
    if not -(2**63) <= result < 2**63:
        raise ValueError(f'value out of range: "{value}"')
    return result


def parse_date(value: str) -> date:
    if len(value) != 6:
        raise ValueError("date string is not valid length")
    try:
        year = parse_num("20" + value[1:3])
        day_of_year = parse_num(value[3:])
        return date(year, 1, 1) + timedelta(days=day_of_year - 1)
    except (ValueError, OverflowError) as exc:
        raise ValueError(f"invalid EFT date: {value}") from exc


def eft_date(value: date | datetime | None) -> str:
    if value is None:
        return "000000"
    return f"{value.year % 100:03d}{value.timetuple().tm_yday:03d}"


def zero_pad_number(value: int, width: int) -> str:
    text = str(value)
    if text.startswith("-"):
        return "-" + text[1:].zfill(max(0, width - 1))
    return text.zfill(width)


def pad_numeric(value: str, width: int) -> str:
    return value.zfill(width)


def pad_numeric_trailing(value: str, width: int) -> str:
    return value + "0" * max(0, width - len(value))


def pad_blanks(value: str, width: int) -> str:
    return value + " " * max(0, width - len(value))


def abbreviate(value: str, width: int) -> str:
    return pad_blanks(value[:width], width)


def filler(width: int) -> str:
    return " " * max(0, width)


def _as_date(value: date | datetime | str | None) -> date | datetime | None:
    if value is None or isinstance(value, (date, datetime)):
        return value
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed


def _json_date(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        result = value.isoformat(timespec="microseconds" if value.microsecond else "seconds")
        result = re.sub(r"(\.\d*?)0+(?=[+-]|$)", r"\1", result)
        return result.replace("+00:00", "Z")
    return datetime.combine(value, datetime.min.time(), timezone.utc).isoformat(
        timespec="seconds"
    ).replace("+00:00", "Z")


@dataclass
class RecordHeader:
    record_type: str = ""
    originator_id: str = ""
    file_creation_number: int = 0
    record_count: int = 0

    def parse(self, line: str) -> None:
        chars = list(line)
        if len(chars) < COMMON_RECORD_DATA_LENGTH:
            raise ParseError("record header line too short")
        record_type = chars[0]
        if record_type not in {HEADER_RECORD, CREDIT_RECORD, DEBIT_RECORD, FOOTER_RECORD}:
            raise ParseError(f"unrecognized record type {record_type}")
        try:
            record_count = parse_num("".join(chars[1:10]))
            creation_number = parse_num("".join(chars[20:24]))
        except ValueError as exc:
            raise ParseError(str(exc)) from exc
        self.record_type = record_type
        self.record_count = record_count
        self.originator_id = "".join(chars[10:20]).strip()
        self.file_creation_number = creation_number

    def build(self) -> str:
        return (
            abbreviate(self.record_type, 1)
            + zero_pad_number(self.record_count, 9)
            + pad_numeric(self.originator_id, 10)
            + zero_pad_number(self.file_creation_number, 4)
        )

    Build = build

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.record_type,
            "originator_id": self.originator_id,
            "file_creation_number": self.file_creation_number,
        }


@dataclass
class BaseTxn:
    txn_type: str = ""
    amount: int = 0
    item_trace_no: str = ""
    institution_id: str = ""
    stored_txn_type: str = ""
    short_name: str = ""
    long_name: str = ""
    user_id: str = ""
    cross_ref_no: str = ""
    sundry_info: str = ""
    settlement_code: str = ""
    invalid_data_element_id: str = ""
    record_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "txn_type": self.txn_type,
            "amount": self.amount,
            "item_trace_no": self.item_trace_no,
            "institution_id": self.institution_id,
            "stored_txn_type": self.stored_txn_type,
            "short_name": self.short_name,
            "long_name": self.long_name,
            "user_id": self.user_id,
            "cross_ref_no": self.cross_ref_no,
            "sundry_info": self.sundry_info,
            "settlement_code": self.settlement_code,
            "invalid_data_element_id": self.invalid_data_element_id,
            "type": self.record_type,
        }


@dataclass
class Transaction(BaseTxn):
    """Shared API for a 240-character EFT transaction segment."""

    date_value: date | datetime | None = None
    account_no: str = ""
    name: str = ""
    return_institution_id: str = ""
    return_account_no: str = ""
    original_institution_id: str = ""
    original_account_no: str = ""
    original_item_trace_no: str = ""

    RECORD_TYPE = ""
    DATE_JSON = ""
    ACCOUNT_JSON = ""
    NAME_JSON = ""
    HAS_RETURN = False
    HAS_ORIGINAL = False
    HAS_ORIGINAL_ITEM = False
    HAS_INVALID_ID = False
    DATE_ERROR = "failed to parse due date"
    NAME_ERROR = "failed to format payor name"

    def __post_init__(self) -> None:
        self.record_type = self.RECORD_TYPE

    def get_type(self) -> str:
        return self.RECORD_TYPE

    def get_amount(self) -> int:
        return self.amount

    def get_base_txn(self) -> BaseTxn:
        return BaseTxn(
            self.txn_type, self.amount, self.item_trace_no, self.institution_id,
            self.stored_txn_type, self.short_name, self.long_name, self.user_id,
            self.cross_ref_no, self.sundry_info, self.settlement_code,
            self.invalid_data_element_id, self.record_type,
        )

    def get_account_no(self) -> str:
        return self.account_no

    def get_date(self) -> date | datetime | None:
        return self.date_value

    def get_name(self) -> str:
        return self.name

    def get_return_institution_id(self) -> str:
        return self.return_institution_id if self.HAS_RETURN else ""

    def get_return_account_no(self) -> str:
        return self.return_account_no if self.HAS_RETURN else ""

    def get_original_institution_id(self) -> str:
        return self.original_institution_id if self.HAS_ORIGINAL else ""

    def get_original_account_no(self) -> str:
        return self.original_account_no if self.HAS_ORIGINAL else ""

    def get_original_item_trace_no(self) -> str:
        return self.original_item_trace_no if self.HAS_ORIGINAL_ITEM else ""

    def build(self) -> str:
        def name_field(value: str, width: int, error: str) -> str:
            del error
            return abbreviate(normalize(value), width)

        def date_field() -> str:
            return pad_numeric(eft_date(self.date_value), 6)

        out = [
            pad_numeric(self.txn_type, 3),
            zero_pad_number(self.amount, 10),
            date_field(),
            pad_numeric(self.institution_id, 9),
            abbreviate(self.account_no, 12),
            pad_numeric(self.item_trace_no, 22),
            pad_numeric(self.stored_txn_type, 3),
            name_field(self.short_name, 15, "failed to format originator short name"),
            name_field(self.name, 30, self.NAME_ERROR),
            name_field(self.long_name, 30, "failed to format originator long name"),
            abbreviate(self.user_id, 10),
            abbreviate(self.cross_ref_no, 19),
        ]
        if self.HAS_ORIGINAL:
            out.extend(
                [
                    pad_numeric(self.original_institution_id, 9),
                    abbreviate(self.original_account_no, 12),
                ]
            )
        else:
            out.extend(
                [
                    pad_numeric(self.return_institution_id, 9),
                    abbreviate(self.return_account_no, 12),
                ]
            )
        out.append(abbreviate(self.sundry_info, 15))
        if self.HAS_ORIGINAL_ITEM:
            out.append(pad_numeric(self.original_item_trace_no, 22))
        else:
            out.append(filler(22))
        out.append(abbreviate(self.settlement_code, 2))
        if self.HAS_INVALID_ID:
            out.append(pad_numeric_trailing(self.invalid_data_element_id, 11))
        else:
            out.append(pad_numeric("", 11))
        return "".join(out)

    def parse(self, segment: str) -> Transaction:
        segment = normalize(segment)
        chars = list(segment)
        if len(chars) != SEGMENT_LENGTH:
            raise ParseError("transaction record is not 240 characters")
        self.txn_type = "".join(chars[0:3])
        try:
            self.amount = parse_num("".join(chars[3:13]))
        except ValueError as exc:
            raise ParseError(f"failed to parse amount: {exc}") from exc
        try:
            self.date_value = parse_date("".join(chars[13:19]))
        except ValueError as exc:
            raise ParseError(f"{self.DATE_ERROR}: {exc}") from exc
        self.institution_id = "".join(chars[19:28])
        self.account_no = "".join(chars[28:40]).strip()
        self.item_trace_no = "".join(chars[40:62])
        self.stored_txn_type = "".join(chars[62:65])
        self.short_name = "".join(chars[65:80]).strip()
        self.name = "".join(chars[80:110]).strip()
        self.long_name = "".join(chars[110:140]).strip()
        self.user_id = "".join(chars[140:150]).strip()
        self.cross_ref_no = "".join(chars[150:169]).strip()
        if self.HAS_ORIGINAL:
            self.original_institution_id = "".join(chars[169:178]).strip()
            self.original_account_no = "".join(chars[178:190]).strip()
        else:
            self.return_institution_id = "".join(chars[169:178]).strip()
            self.return_account_no = "".join(chars[178:190]).strip()
        self.sundry_info = "".join(chars[190:205]).strip()
        if self.HAS_ORIGINAL_ITEM:
            self.original_item_trace_no = "".join(chars[205:227]).strip()
        self.settlement_code = "".join(chars[227:229]).strip()
        if self.HAS_INVALID_ID:
            self.invalid_data_element_id = "".join(chars[229:240]).strip()
        self.record_type = self.RECORD_TYPE
        return self

    def validate(self) -> None:
        errors: list[str] = []

        def required(name: str, value: Any) -> None:
            if value is None or value == "" or value == 0:
                errors.append(f"{name} is required")

        def max_len(name: str, value: str, maximum: int) -> None:
            if len(value) > maximum:
                errors.append(f"{name} exceeds max length {maximum}")

        def numeric(name: str, value: str) -> None:
            if value and not re.fullmatch(r"[0-9]+", value):
                errors.append(f"{name} must be numeric")

        def alpha(name: str, value: str) -> None:
            if value and not re.fullmatch(r"[A-Za-z0-9_\-\t\n\f\r ]+", value):
                errors.append(f"{name} must contain EFT alphanumeric characters")

        required("txn_type", self.txn_type)
        numeric("txn_type", self.txn_type)
        max_len("txn_type", self.txn_type, 3)
        required("amount", self.amount)
        if self.amount > 9_999_999_999:
            errors.append("amount exceeds maximum")
        numeric("item_trace_no", self.item_trace_no)
        max_len("item_trace_no", self.item_trace_no, 22)
        required("institution_id", self.institution_id)
        numeric("institution_id", self.institution_id)
        max_len("institution_id", self.institution_id, 9)
        numeric("stored_txn_type", self.stored_txn_type)
        max_len("stored_txn_type", self.stored_txn_type, 3)
        max_len("short_name", self.short_name, 15)
        required("long_name", self.long_name)
        max_len("long_name", self.long_name, 30)
        alpha("user_id", self.user_id)
        max_len("user_id", self.user_id, 10)
        alpha("cross_ref_no", self.cross_ref_no)
        max_len("cross_ref_no", self.cross_ref_no, 19)
        alpha("sundry_info", self.sundry_info)
        max_len("sundry_info", self.sundry_info, 15)
        alpha("settlement_code", self.settlement_code)
        max_len("settlement_code", self.settlement_code, 2)
        numeric("invalid_data_element_id", self.invalid_data_element_id)
        max_len("invalid_data_element_id", self.invalid_data_element_id, 11)
        if self.record_type not in _VALID_RECORD_TYPES:
            errors.append("invalid record type")
        required(self.DATE_JSON, self.date_value)
        if self.date_value is not None and self.DATE_JSON == "date_funds_available":
            pass
        required(self.ACCOUNT_JSON, self.account_no)
        max_len(self.ACCOUNT_JSON, self.account_no, 12)
        numeric(self.ACCOUNT_JSON, self.account_no)
        required(self.NAME_JSON, self.name)
        max_len(self.NAME_JSON, self.name, 30)
        if self.HAS_RETURN:
            required("return_institution_id", self.return_institution_id)
            max_len("return_institution_id", self.return_institution_id, 9)
            numeric("return_institution_id", self.return_institution_id)
            required("return_account_no", self.return_account_no)
            max_len("return_account_no", self.return_account_no, 12)
            alpha("return_account_no", self.return_account_no)
        if self.HAS_ORIGINAL:
            required("original_institution_id", self.original_institution_id)
            max_len("original_institution_id", self.original_institution_id, 9)
            numeric("original_institution_id", self.original_institution_id)
            required("original_account_no", self.original_account_no)
            max_len("original_account_no", self.original_account_no, 12)
            alpha("original_account_no", self.original_account_no)
        if self.HAS_ORIGINAL_ITEM:
            required("original_item_trace_no", self.original_item_trace_no)
            numeric("original_item_trace_no", self.original_item_trace_no)
            max_len("original_item_trace_no", self.original_item_trace_no, 22)
        if errors:
            raise ValidationError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        result = super().to_dict()
        result[self.DATE_JSON] = _json_date(self.date_value)
        result[self.ACCOUNT_JSON] = self.account_no
        result[self.NAME_JSON] = self.name
        if self.HAS_RETURN:
            result["return_institution_id"] = self.return_institution_id
            result["return_account_no"] = self.return_account_no
        if self.HAS_ORIGINAL:
            result["original_institution_id"] = self.original_institution_id
            result["original_account_no"] = self.original_account_no
        if self.HAS_ORIGINAL_ITEM:
            result["original_item_trace_no"] = self.original_item_trace_no
        return result

    # Keep Go API spellings available for straightforward porting.
    Build = build
    Parse = parse
    Validate = validate
    GetType = get_type
    GetAmount = get_amount
    GetBaseTxn = get_base_txn
    GetAccountNo = get_account_no
    GetDate = get_date
    GetName = get_name
    GetReturnInstitutionID = get_return_institution_id
    GetReturnAccountNo = get_return_account_no
    GetOriginalInstitutionID = get_original_institution_id
    GetOriginalAccountNo = get_original_account_no
    GetOriginalItemTraceNo = get_original_item_trace_no


@dataclass
class Debit(Transaction):
    due_date: date | datetime | None = None
    payor_account_no: str = ""
    payor_name: str = ""
    return_institution_id: str = ""
    return_account_no: str = ""

    RECORD_TYPE = DEBIT_RECORD
    DATE_JSON = "due_date"
    ACCOUNT_JSON = "payor_account_no"
    NAME_JSON = "payor_name"
    HAS_RETURN = True
    DATE_ERROR = "failed to parse due date"
    NAME_ERROR = "failed to format payor name"

    def __post_init__(self) -> None:
        self.date_value = self.due_date
        self.account_no = self.payor_account_no
        self.name = self.payor_name
        super().__post_init__()

    def to_dict(self) -> dict[str, Any]:
        self.date_value, self.account_no, self.name = self.due_date, self.payor_account_no, self.payor_name
        return super().to_dict()

    def parse(self, segment: str) -> Debit:
        super().parse(segment)
        self.due_date, self.payor_account_no, self.payor_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


@dataclass
class Credit(Transaction):
    date_funds_available: date | datetime | None = None
    payee_account_no: str = ""
    payee_name: str = ""
    return_institution_id: str = ""
    return_account_no: str = ""

    RECORD_TYPE = CREDIT_RECORD
    DATE_JSON = "date_funds_available"
    ACCOUNT_JSON = "payee_account_no"
    NAME_JSON = "payee_name"
    HAS_RETURN = True
    DATE_ERROR = "failed to parse date funds available"
    NAME_ERROR = "failed to format payee name"

    def __post_init__(self) -> None:
        self.date_value, self.account_no, self.name = self.date_funds_available, self.payee_account_no, self.payee_name
        super().__post_init__()

    def to_dict(self) -> dict[str, Any]:
        self.date_value, self.account_no, self.name = self.date_funds_available, self.payee_account_no, self.payee_name
        return super().to_dict()

    def parse(self, segment: str) -> Credit:
        super().parse(segment)
        self.date_funds_available, self.payee_account_no, self.payee_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


@dataclass
class DebitReturn(Transaction):
    due_date: date | datetime | None = None
    payor_account_no: str = ""
    payor_name: str = ""
    original_institution_id: str = ""
    original_account_no: str = ""
    original_item_trace_no: str = ""

    RECORD_TYPE = RETURN_DEBIT_RECORD
    DATE_JSON = "due_date"
    ACCOUNT_JSON = "payor_account_no"
    NAME_JSON = "payor_name"
    HAS_ORIGINAL = True
    HAS_ORIGINAL_ITEM = True
    HAS_INVALID_ID = True

    def __post_init__(self) -> None:
        self.date_value, self.account_no, self.name = self.due_date, self.payor_account_no, self.payor_name
        super().__post_init__()

    def to_dict(self) -> dict[str, Any]:
        self.date_value, self.account_no, self.name = self.due_date, self.payor_account_no, self.payor_name
        return super().to_dict()

    def parse(self, segment: str) -> DebitReturn:
        super().parse(segment)
        self.due_date, self.payor_account_no, self.payor_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


@dataclass
class CreditReturn(Transaction):
    date_funds_available: date | datetime | None = None
    payee_account_no: str = ""
    payee_name: str = ""
    original_institution_id: str = ""
    original_account_no: str = ""
    original_item_trace_no: str = ""

    RECORD_TYPE = RETURN_CREDIT_RECORD
    DATE_JSON = "date_funds_available"
    ACCOUNT_JSON = "payee_account_no"
    NAME_JSON = "payee_name"
    HAS_ORIGINAL = True
    HAS_ORIGINAL_ITEM = True
    HAS_INVALID_ID = True
    DATE_ERROR = "failed to parse date funds available"
    NAME_ERROR = "failed to format payee name"

    def __post_init__(self) -> None:
        self.date_value, self.account_no, self.name = self.date_funds_available, self.payee_account_no, self.payee_name
        super().__post_init__()

    def to_dict(self) -> dict[str, Any]:
        self.date_value, self.account_no, self.name = self.date_funds_available, self.payee_account_no, self.payee_name
        return super().to_dict()

    def parse(self, segment: str) -> CreditReturn:
        super().parse(segment)
        self.date_funds_available, self.payee_account_no, self.payee_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


@dataclass
class CreditReverse(Credit):
    original_item_trace_no: str = ""

    RECORD_TYPE = CREDIT_REVERSE_RECORD
    HAS_ORIGINAL_ITEM = True

    def parse(self, segment: str) -> CreditReverse:
        Transaction.parse(self, segment)
        self.date_funds_available, self.payee_account_no, self.payee_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


@dataclass
class DebitReverse(Debit):
    original_item_trace_no: str = ""

    RECORD_TYPE = DEBIT_REVERSE_RECORD
    HAS_ORIGINAL_ITEM = True

    def parse(self, segment: str) -> DebitReverse:
        Transaction.parse(self, segment)
        self.due_date, self.payor_account_no, self.payor_name = self.date_value, self.account_no, self.name
        return self

    Parse = parse


_TRANSACTION_CLASS = {
    DEBIT_RECORD: Debit,
    CREDIT_RECORD: Credit,
    RETURN_DEBIT_RECORD: DebitReturn,
    RETURN_CREDIT_RECORD: CreditReturn,
    CREDIT_REVERSE_RECORD: CreditReverse,
    DEBIT_REVERSE_RECORD: DebitReverse,
}


def _new_transaction(txn_record_type: str, **values: Any) -> Transaction:
    cls = _TRANSACTION_CLASS[txn_record_type]
    return cls(**values)


def _make_base(txn_type: str, amount: int, item_trace_no: str, institution_id: str,
               short_name: str, long_name: str, record_type: str,
               options: dict[str, str]) -> dict[str, Any]:
    return {
        "txn_type": txn_type,
        "amount": amount,
        "item_trace_no": item_trace_no,
        "institution_id": institution_id,
        "stored_txn_type": options.get("stored_txn_type", "000"),
        "short_name": short_name,
        "long_name": long_name,
        "user_id": options.get("user_id", ""),
        "cross_ref_no": options.get("cross_ref_no", ""),
        "sundry_info": options.get("sundry_info", ""),
        "settlement_code": options.get("settlement_code", ""),
        "invalid_data_element_id": options.get("invalid_data_element_id", ""),
        "record_type": record_type,
    }


def _constructor(record_type: str, txn_type: str, amount: int,
                 txn_date: date | datetime | None, institution_id: str,
                 account_no: str, item_trace_no: str, short_name: str,
                 name: str, long_name: str, bank_id: str, bank_account: str,
                 original_item_trace_no: str = "", *opts: Any,
                 **options: str) -> Transaction:
    options = dict(options)
    for option in opts:
        if callable(option):
            option(options)
        elif isinstance(option, dict):
            options.update(option)
        else:
            raise TypeError(f"unsupported transaction option: {option!r}")
    date_key = "date_funds_available" if record_type in {
        CREDIT_RECORD, CREDIT_REVERSE_RECORD, RETURN_CREDIT_RECORD
    } else "due_date"
    account_key = "payee_account_no" if record_type in {
        CREDIT_RECORD, CREDIT_REVERSE_RECORD, RETURN_CREDIT_RECORD
    } else "payor_account_no"
    name_key = "payee_name" if account_key == "payee_account_no" else "payor_name"
    values = _make_base(txn_type, amount, item_trace_no, institution_id,
                        short_name, long_name, record_type, options)
    values.update({date_key: txn_date, account_key: account_no, name_key: name})
    if record_type in {DEBIT_RECORD, CREDIT_RECORD, DEBIT_REVERSE_RECORD, CREDIT_REVERSE_RECORD}:
        values["return_institution_id"] = bank_id
        values["return_account_no"] = bank_account
    else:
        values["original_institution_id"] = bank_id
        values["original_account_no"] = bank_account
    if record_type in {DEBIT_RETURN_RECORD, CREDIT_RETURN_RECORD, DEBIT_REVERSE_RECORD, CREDIT_REVERSE_RECORD}:
        values["original_item_trace_no"] = original_item_trace_no
    return _new_transaction(record_type, **values)


def NewDebit(txn_type: str, amount: int, due_date: date | datetime | None,
             institution_id: str, payor_account_no: str, item_trace_no: str,
             originator_short_name: str, payor_name: str, originator_long_name: str,
             return_institution_id: str, return_account_no: str, *opts: Any,
             **options: str) -> Debit:
    return _constructor(DEBIT_RECORD, txn_type, amount, due_date, institution_id,
                        payor_account_no, item_trace_no, originator_short_name, payor_name,
                        originator_long_name, return_institution_id, return_account_no, "",
                        *opts, **options)


def NewCredit(txn_type: str, amount: int, date_funds_available: date | datetime | None,
              institution_id: str, payee_account_no: str, item_trace_no: str,
              originator_short_name: str, payee_name: str, originator_long_name: str,
              return_institution_id: str, return_account_no: str, *opts: Any,
              **options: str) -> Credit:
    return _constructor(CREDIT_RECORD, txn_type, amount, date_funds_available, institution_id,
                        payee_account_no, item_trace_no, originator_short_name, payee_name,
                        originator_long_name, return_institution_id, return_account_no, "", *opts, **options)


def NewDebitReturn(txn_type: str, amount: int, due_date: date | datetime | None,
                   institution_id: str, payor_account_no: str, item_trace_no: str,
                   originator_short_name: str, payor_name: str, originator_long_name: str,
                   original_institution_id: str, original_account_no: str,
                   original_item_trace_no: str, *opts: Any, **options: str) -> DebitReturn:
    return _constructor(RETURN_DEBIT_RECORD, txn_type, amount, due_date, institution_id,
                        payor_account_no, item_trace_no, originator_short_name, payor_name,
                        originator_long_name, original_institution_id, original_account_no,
                        original_item_trace_no, *opts, **options)


def NewCreditReturn(txn_type: str, amount: int, date_funds_available: date | datetime | None,
                    institution_id: str, payee_account_no: str, item_trace_no: str,
                    originator_short_name: str, payee_name: str, originator_long_name: str,
                    original_institution_id: str, original_account_no: str,
                    original_item_trace_no: str, *opts: Any, **options: str) -> CreditReturn:
    return _constructor(RETURN_CREDIT_RECORD, txn_type, amount, date_funds_available, institution_id,
                        payee_account_no, item_trace_no, originator_short_name, payee_name,
                        originator_long_name, original_institution_id, original_account_no,
                        original_item_trace_no, *opts, **options)


def NewCreditReverse(txn_type: str, amount: int, date_funds_available: date | datetime | None,
                     institution_id: str, payee_account_no: str, item_trace_no: str,
                     originator_short_name: str, payee_name: str, originator_long_name: str,
                     return_institution_id: str, return_account_no: str,
                     original_item_trace_no: str, *opts: Any, **options: str) -> CreditReverse:
    return _constructor(CREDIT_REVERSE_RECORD, txn_type, amount, date_funds_available, institution_id,
                        payee_account_no, item_trace_no, originator_short_name, payee_name,
                        originator_long_name, return_institution_id, return_account_no,
                        original_item_trace_no, *opts, **options)


def NewDebitReverse(txn_type: str, amount: int, due_date: date | datetime | None,
                    institution_id: str, payor_account_no: str, item_trace_no: str,
                    originator_short_name: str, payor_name: str, originator_long_name: str,
                    return_institution_id: str, return_account_no: str,
                    original_item_trace_no: str, *opts: Any, **options: str) -> DebitReverse:
    return _constructor(DEBIT_REVERSE_RECORD, txn_type, amount, due_date, institution_id,
                        payor_account_no, item_trace_no, originator_short_name, payor_name,
                        originator_long_name, return_institution_id, return_account_no,
                        original_item_trace_no, *opts, **options)


@dataclass
class FileHeader:
    record_header: RecordHeader = None  # type: ignore[assignment]
    creation_date: date | datetime | None = None
    destination_data_center: int = 0
    communication_area: str = ""
    currency_code: str = ""

    def __post_init__(self) -> None:
        if self.record_header is None:
            self.record_header = RecordHeader(HEADER_RECORD, "", 0, 1)

    def parse(self, line: str) -> None:
        chars = list(line)
        if len(chars) < 58:
            raise ParseError("invalid header record length")
        self.record_header = RecordHeader()
        self.record_header.parse(line)
        try:
            self.creation_date = parse_date("".join(chars[24:30]))
            self.destination_data_center = parse_num("".join(chars[30:35]))
        except ValueError as exc:
            raise ParseError(str(exc)) from exc
        self.communication_area = "".join(chars[35:55]).strip()
        self.currency_code = "".join(chars[55:58]).strip()

    def build(self) -> str:
        date_text = eft_date(self.creation_date)
        return (
            self.record_header.build()
            + date_text
            + zero_pad_number(self.destination_data_center, 5)
            + pad_blanks(self.communication_area, 20)
            + pad_blanks(self.currency_code, 3)
            + filler(1406)
        )

    def validate(self) -> None:
        errors = []
        rh = self.record_header
        if rh.record_type not in _VALID_RECORD_TYPES:
            errors.append("invalid record type")
        if not rh.originator_id:
            errors.append("missing originator ID")
        if len(rh.originator_id) != 10:
            errors.append("originator ID must be 10 characters")
        if rh.originator_id and not re.fullmatch(r"[A-Za-z0-9_\-\t\n\f\r ]+", rh.originator_id):
            errors.append("originator ID must be alphanumeric")
        if rh.file_creation_number == 0 or rh.file_creation_number > 9999:
            errors.append("invalid file creation number")
        if self.creation_date is None:
            errors.append("missing creation date")
        if not 0 <= self.destination_data_center <= 99999:
            errors.append("invalid destination data center")
        if len(self.communication_area) > 20:
            errors.append("communication area exceeds 20 characters")
        if self.currency_code not in {"CAD", "USD"}:
            errors.append("invalid currency code")
        if errors:
            raise ValidationError("; ".join(errors))

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.record_header.to_dict(),
            "creation_date": _json_date(self.creation_date),
            "destination_data_center": self.destination_data_center,
            "communication_area": self.communication_area,
            "currency_code": self.currency_code,
        }

    @property
    def originator_id(self) -> str:
        return self.record_header.originator_id

    @originator_id.setter
    def originator_id(self, value: str) -> None:
        self.record_header.originator_id = value

    @property
    def record_type(self) -> str:
        return self.record_header.record_type

    @property
    def file_creation_number(self) -> int:
        return self.record_header.file_creation_number

    Parse = parse
    Build = build
    Validate = validate


def NewFileHeader(originator_id: str, file_creation_number: int,
                  creation_date: date | datetime | None, destination_data_center: int,
                  currency_code: str, *opts: Any, **options: str) -> FileHeader:
    for option in opts:
        if isinstance(option, dict):
            options.update(option)
        elif callable(option):
            option(options)
        else:
            raise TypeError(f"unsupported header option: {option!r}")
    return FileHeader(
        RecordHeader(HEADER_RECORD, originator_id, file_creation_number, 1),
        creation_date,
        destination_data_center,
        options.get("communication_area", ""),
        currency_code,
    )


@dataclass
class FileFooter:
    record_header: RecordHeader = None  # type: ignore[assignment]
    total_value_debit: int = 0
    total_count_debit: int = 0
    total_value_credit: int = 0
    total_count_credit: int = 0
    total_value_reverse_debit: int = 0
    total_count_reverse_debit: int = 0
    total_value_reverse_credit: int = 0
    total_count_reverse_credit: int = 0

    def __post_init__(self) -> None:
        if self.record_header is None:
            self.record_header = RecordHeader(FOOTER_RECORD)

    @classmethod
    def from_transactions(cls, record_header: RecordHeader, txns: list[Transaction]) -> FileFooter:
        def totals(*record_types: str) -> tuple[int, int]:
            selected = [txn for txn in txns if txn.get_type() in record_types]
            return sum(t.amount for t in selected), len(selected)

        debit_value, debit_count = totals(DEBIT_RECORD, RETURN_DEBIT_RECORD)
        credit_value, credit_count = totals(CREDIT_RECORD, RETURN_CREDIT_RECORD)
        reverse_credit_value, reverse_credit_count = totals(CREDIT_REVERSE_RECORD)
        reverse_debit_value, reverse_debit_count = totals(DEBIT_REVERSE_RECORD)
        return cls(record_header, debit_value, debit_count, credit_value, credit_count,
                   reverse_credit_value, reverse_credit_count,
                   reverse_debit_value, reverse_debit_count)

    def parse(self, line: str) -> None:
        chars = list(line)
        if len(chars) < 112:
            raise ParseError("footer is too short")
        self.record_header = RecordHeader()
        self.record_header.parse(line)
        offsets = [(24, 38), (38, 46), (46, 60), (60, 68),
                   (68, 82), (82, 90), (90, 104), (104, 112)]
        values = []
        for start, end in offsets:
            piece = "".join(chars[start:end])
            if not piece.strip():
                values.append(0)
                continue
            try:
                values.append(parse_num(piece))
            except ValueError as exc:
                raise ParseError(f"failed to parse footer total at {start}:{end}: {exc}") from exc
        (
            self.total_value_debit, self.total_count_debit,
            self.total_value_credit, self.total_count_credit,
            self.total_value_reverse_debit, self.total_count_reverse_debit,
            self.total_value_reverse_credit, self.total_count_reverse_credit,
        ) = values

    def build(self) -> str:
        return (
            self.record_header.build()
            + zero_pad_number(self.total_value_debit, 14)
            + zero_pad_number(self.total_count_debit, 8)
            + zero_pad_number(self.total_value_credit, 14)
            + zero_pad_number(self.total_count_credit, 8)
            + zero_pad_number(self.total_value_reverse_debit, 14)
            + zero_pad_number(self.total_count_reverse_debit, 8)
            + zero_pad_number(self.total_value_reverse_credit, 14)
            + zero_pad_number(self.total_count_reverse_credit, 8)
            + filler(1352)
        )

    def get_type(self) -> str:
        return FOOTER_RECORD

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.record_header.to_dict(),
            "total_value_debit": self.total_value_debit,
            "total_count_debit": self.total_count_debit,
            "total_value_credit": self.total_value_credit,
            "total_count_credit": self.total_count_credit,
            "total_value_reverse_debit": self.total_value_reverse_debit,
            "total_count_reverse_debit": self.total_count_reverse_debit,
            "total_value_reverse_credit": self.total_value_reverse_credit,
            "total_count_reverse_credit": self.total_count_reverse_credit,
        }

    @property
    def originator_id(self) -> str:
        return self.record_header.originator_id

    @property
    def record_type(self) -> str:
        return self.record_header.record_type

    @property
    def file_creation_number(self) -> int:
        return self.record_header.file_creation_number

    Parse = parse
    Build = build
    GetType = get_type


def NewFileFooter(record_header: RecordHeader, txns: list[Transaction]) -> FileFooter:
    return FileFooter.from_transactions(record_header, txns)


@dataclass
class File:
    header: FileHeader | None = None
    txns: list[Transaction] | None = None
    footer: FileFooter | None = None

    def __post_init__(self) -> None:
        if self.txns is None:
            self.txns = []

    def create(self) -> str:
        if self.header is None:
            raise CadeftError("file header is missing")
        current_line = 1
        output = [self.header.build()]
        current_line += 1
        txns = self.txns or []
        # The Go implementation groups records by type. Keep first-seen type ordering,
        # which is stable across runtimes unlike Go map iteration.
        grouped: dict[str, list[Transaction]] = {}
        for index, txn in enumerate(txns):
            if txn.get_type() not in _RECORD_TYPES:
                raise CadeftError(f"transaction[{index}] has unexpected record type: {txn.get_type()}")
            grouped.setdefault(txn.get_type(), []).append(txn)
        for record_type, entries in grouped.items():
            for offset in range(0, len(entries), MAX_TXNS_PER_RECORD):
                header = RecordHeader(record_type, self.header.record_header.originator_id,
                                      self.header.record_header.file_creation_number, current_line)
                built = "".join(txn.build() for txn in entries[offset:offset + MAX_TXNS_PER_RECORD])
                output.append("\n" + header.build() + built + filler(MAX_LINE_LENGTH - 24 - len(built)))
                current_line += 1
        output.append("\n")
        if self.footer is None:
            rh = RecordHeader(
                FOOTER_RECORD,
                self.header.record_header.originator_id,
                self.header.record_header.file_creation_number,
                current_line,
            )
            self.footer = FileFooter.from_transactions(rh, txns)
        else:
            self.footer.record_header.record_count = current_line
        output.append(self.footer.build())
        return "".join(output)

    def validate(self) -> None:
        if self.header is None:
            raise ValidationError("file header is missing")
        errors = []
        try:
            self.header.validate()
        except ValidationError as exc:
            errors.append(str(exc))
        for index, txn in enumerate(self.txns or []):
            try:
                txn.validate()
            except ValidationError as exc:
                errors.append(f"failed to validate txn {index}: {exc}")
        if self.footer is not None:
            expected = FileFooter.from_transactions(self.footer.record_header, self.txns or [])
            for field_name in (
                "total_value_debit", "total_count_debit", "total_value_credit",
                "total_count_credit", "total_value_reverse_debit",
                "total_count_reverse_debit", "total_value_reverse_credit",
                "total_count_reverse_credit",
            ):
                actual = getattr(self.footer, field_name)
                wanted = getattr(expected, field_name)
                if actual != wanted:
                    errors.append(f"footer {field_name}: got {actual} want {wanted}")
        if errors:
            raise ValidationError("; ".join(errors))

    def get_all_debit_txns(self) -> list[Debit]:
        return [txn for txn in self.txns or [] if isinstance(txn, Debit) and not isinstance(txn, DebitReverse)]

    def get_all_credits(self) -> list[Credit]:
        return [txn for txn in self.txns or [] if isinstance(txn, Credit) and not isinstance(txn, CreditReverse)]

    def get_all_debit_returns(self) -> list[DebitReturn]:
        return [txn for txn in self.txns or [] if isinstance(txn, DebitReturn)]

    def get_all_credit_returns(self) -> list[CreditReturn]:
        return [txn for txn in self.txns or [] if isinstance(txn, CreditReturn)]

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.header is not None:
            result["file_header"] = self.header.to_dict()
        if self.txns:
            result["transactions"] = [txn.to_dict() for txn in self.txns]
        if self.footer is not None:
            result["file_footer"] = self.footer.to_dict()
        return result

    Create = create
    Validate = validate
    GetAllDebitTxns = get_all_debit_txns
    GetAllCredits = get_all_credits
    GetAllDebitReturns = get_all_debit_returns
    GetAllCreditReturns = get_all_credit_returns


def NewFile(header: FileHeader, txns: list[Transaction]) -> File:
    return File(header, txns)


def NewReader(source: str | bytes | TextIO | BinaryIO) -> Reader:
    return Reader(source)


def NewFileStream(source: str | TextIO | BinaryIO) -> FileStreamer:
    return FileStreamer(source)


new_debit = NewDebit
new_credit = NewCredit
new_debit_return = NewDebitReturn
new_credit_return = NewCreditReturn
new_debit_reverse = NewDebitReverse
new_credit_reverse = NewCreditReverse
new_file = NewFile
new_file_header = NewFileHeader
new_file_footer = NewFileFooter
new_reader = NewReader
new_file_stream = NewFileStream


def transaction_from_dict(value: dict[str, Any]) -> Transaction:
    record_type = value.get("type", "")
    cls = _TRANSACTION_CLASS.get(record_type)
    if cls is None:
        raise ParseError(f"unknown transaction type {record_type!r}")
    aliases = {field.name for field in fields(cls)}
    cooked = {key: val for key, val in value.items() if key in aliases}
    date_key = cls.DATE_JSON
    if date_key in cooked:
        cooked[date_key] = _as_date(cooked[date_key])
    return cls(**cooked)


def file_from_dict(value: dict[str, Any]) -> File:
    header_data = value.get("file_header")
    header = None
    if header_data is not None:
        rh = RecordHeader(
            header_data.get("type", ""),
            header_data.get("originator_id", ""),
            header_data.get("file_creation_number", 0),
            header_data.get("record_count", 0),
        )
        header = FileHeader(
            rh,
            _as_date(header_data.get("creation_date")),
            header_data.get("destination_data_center", 0),
            header_data.get("communication_area", ""),
            header_data.get("currency_code", ""),
        )
    txns = [transaction_from_dict(item) for item in value.get("transactions", [])]
    footer_data = value.get("file_footer")
    footer = None
    if footer_data is not None:
        rh = RecordHeader(
            footer_data.get("type", ""),
            footer_data.get("originator_id", ""),
            footer_data.get("file_creation_number", 0),
            footer_data.get("record_count", 0),
        )
        footer = FileFooter(
            rh,
            footer_data.get("total_value_debit", 0),
            footer_data.get("total_count_debit", 0),
            footer_data.get("total_value_credit", 0),
            footer_data.get("total_count_credit", 0),
            footer_data.get("total_value_reverse_debit", 0),
            footer_data.get("total_count_reverse_debit", 0),
            footer_data.get("total_value_reverse_credit", 0),
            footer_data.get("total_count_reverse_credit", 0),
        )
    return File(header, txns, footer)


class Reader:
    def __init__(self, source: str | bytes | TextIO | BinaryIO):
        self.source = source
        self.file = File()

    def read_file(self) -> File:
        if isinstance(self.source, bytes):
            text = self.source.decode("utf-8")
        elif isinstance(self.source, str):
            text = self.source
        else:
            text = self.source.read()
            if isinstance(text, bytes):
                text = text.decode("utf-8")
        for raw_line in text.splitlines():
            line = normalize(raw_line)
            if not line:
                continue
            record_type = line[0]
            if record_type == HEADER_RECORD:
                if len(line) < 58:
                    raise ParseError("failed to parse header: record type A is not required length")
                header = FileHeader()
                header.parse(line)
                self.file.header = header
            elif record_type in _RECORD_TYPES:
                chars = list(line)
                if len(chars) < COMMON_RECORD_DATA_LENGTH:
                    raise ParseError(
                        f"txn record shorter than common header length {COMMON_RECORD_DATA_LENGTH}: got {len(chars)}"
                    )
                body = chars[COMMON_RECORD_DATA_LENGTH:]
                if len(body) % SEGMENT_LENGTH:
                    raise ParseError(
                        f"record length is not valid multiple of {SEGMENT_LENGTH}, partial txn: {len(body) % SEGMENT_LENGTH}"
                    )
                for offset in range(0, len(body), SEGMENT_LENGTH):
                    segment = "".join(body[offset:offset + SEGMENT_LENGTH])
                    if segment.strip():
                        txn = _TRANSACTION_CLASS[record_type]()
                        txn.parse(segment)
                        self.file.txns.append(txn)
            elif record_type == FOOTER_RECORD:
                if len(line) < 112:
                    raise ParseError("z record does not contain minimum amount of data")
                footer = FileFooter()
                footer.parse(line)
                self.file.footer = footer
        return self.file

    ReadFile = read_file


class FileStreamer:
    """Stateful, single-threaded reader returning one transaction segment at a time."""

    def __init__(self, source: str | TextIO | BinaryIO):
        if isinstance(source, str):
            self._lines = source.splitlines()
        else:
            data = source.read()
            if isinstance(data, bytes):
                data = data.decode("utf-8")
            self._lines = data.splitlines()
        self._line = 0
        self._segments: list[tuple[str, str]] = []

    def get_header(self) -> FileHeader:
        if not self._lines:
            raise ParseError("failed to scan for file header")
        header = FileHeader()
        header.parse(normalize(self._lines[0]))
        return header

    def get_footer(self) -> FileFooter:
        for line in self._lines:
            if line.startswith(FOOTER_RECORD):
                footer = FileFooter()
                footer.parse(normalize(line))
                return footer
        raise ParseError("failed to find footer record")

    def scan_txn(self) -> Transaction:
        if self._line == 0:
            if not self._lines or not self._lines[0].startswith(HEADER_RECORD):
                raise ParseError("first line in file is not a header record")
            self._line = 1
        while not self._segments:
            if self._line >= len(self._lines):
                raise EndOfFile()
            line = normalize(self._lines[self._line].strip())
            self._line += 1
            if not line or line.startswith(FOOTER_RECORD):
                raise EndOfFile()
            if len(line) < COMMON_RECORD_DATA_LENGTH:
                raise ParseError(f"txn record at line {self._line} shorter than common header length 24")
            body = list(line)[COMMON_RECORD_DATA_LENGTH:]
            if len(body) % SEGMENT_LENGTH:
                raise ParseError(f"txn record at line {self._line} is not of correct length")
            record_type = line[0]
            for offset in range(0, len(body), SEGMENT_LENGTH):
                segment = "".join(body[offset:offset + SEGMENT_LENGTH])
                self._segments.append((record_type, segment))
        record_type, segment = self._segments.pop(0)
        cls = _TRANSACTION_CLASS.get(record_type)
        if cls is None:
            raise ParseError(f"unrecognized record type at line {self._line}: {record_type}")
        return cls().parse(segment)

    GetHeader = get_header
    GetFooter = get_footer
    ScanTxn = scan_txn


def NewTransaction(record_type: str, txn_type: str, amount: int,
                   txn_date: date | datetime | None, institution_id: str,
                   account_no: str, item_trace_no: str, short_name: str,
                   name: str, long_name: str, bank_id: str, bank_account: str,
                   original_item_trace_no: str = "", *opts: Any,
                   **options: str) -> Transaction | None:
    if record_type not in _TRANSACTION_CLASS:
        return None
    return _constructor(record_type, txn_type, amount, txn_date, institution_id, account_no,
                        item_trace_no, short_name, name, long_name, bank_id, bank_account,
                        original_item_trace_no, *opts, **options)


new_transaction = NewTransaction


def Ptr(value: Any) -> Any:
    return value


def Arr(value: Any) -> list[Any]:
    return [value]


def _option(name: str, value: str) -> Callable[[dict[str, str]], None]:
    return lambda options: options.__setitem__(name, value)


def WithUserID(value: str) -> Callable[[dict[str, str]], None]:
    return _option("user_id", value)


def WithCrossRefNo(value: str) -> Callable[[dict[str, str]], None]:
    return _option("cross_ref_no", value)


def WithSundryInfo(value: str) -> Callable[[dict[str, str]], None]:
    return _option("sundry_info", value)


def WithSettlementCode(value: str) -> Callable[[dict[str, str]], None]:
    return _option("settlement_code", value)


def WithStoredTransactionType(value: str) -> Callable[[dict[str, str]], None]:
    return _option("stored_txn_type", value)


def WithInvalidDataElementID(value: str) -> Callable[[dict[str, str]], None]:
    return _option("invalid_data_element_id", value)


def WithDirectClearerCommunicationArea(value: str) -> dict[str, str]:
    return {"communication_area": value}


__all__ = [
    "Arr", "BaseTxn", "CREDIT_RECORD", "CREDIT_REVERSE_RECORD", "Credit",
    "CreditReturn", "CreditReverse", "CadeftError", "DEBIT_RECORD",
    "DEBIT_REVERSE_RECORD", "Debit", "DebitReturn", "DebitReverse", "EndOfFile",
    "File", "FileFooter", "FileHeader", "FileStreamer", "FOOTER_RECORD",
    "HEADER_RECORD", "NewCredit", "NewCreditReturn", "NewCreditReverse", "NewDebit",
    "NewDebitReturn", "NewDebitReverse", "NewFile", "NewFileFooter", "NewFileHeader",
    "NewFileStream", "NewReader", "NewTransaction", "ParseError", "Ptr", "Reader",
    "RecordHeader", "RecordType", "TransactionType", "DCSign", "Transactions",
    "BaseTxnOpt", "HeaderOpts",
    "RETURN_CREDIT_RECORD", "RETURN_DEBIT_RECORD", "Transaction", "ValidationError",
    "WithCrossRefNo", "WithDirectClearerCommunicationArea", "WithInvalidDataElementID",
    "WithSettlementCode", "WithStoredTransactionType", "WithSundryInfo", "WithUserID",
    "file_from_dict", "normalize", "parse_date", "parse_num", "transaction_from_dict",
    "new_credit", "new_credit_return", "new_credit_reverse", "new_debit",
    "new_debit_return", "new_debit_reverse", "new_file", "new_file_footer",
    "new_file_header", "new_file_stream", "new_reader", "new_transaction",
]
