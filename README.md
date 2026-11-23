# cadeft

[![Python](https://github.com/protoconal/cadeft/actions/workflows/python.yml/badge.svg)](https://github.com/protoconal/cadeft/actions/workflows/python.yml)
[![Upstream parity](https://github.com/protoconal/cadeft/actions/workflows/upstream-parity.yml/badge.svg)](https://github.com/protoconal/cadeft/actions/workflows/upstream-parity.yml)
[![Apache 2 License](https://img.shields.io/badge/license-Apache2-blue.svg)](LICENSE)

`cadeft` is a dependency-free Python library for reading and writing Electronic Funds Transfer (EFT) files
following the [Payments Canada 005 specification](https://www.payments.ca/sites/default/files/standard005eng.pdf).

It is a Python port of [moov-io/cadeft](https://github.com/moov-io/cadeft) (Go), originally developed by
Moov.IO with major contributions from [Synctera](https://synctera.com/). See [Attribution](#attribution).

## Installation

```sh
pip install cadeft
```

Requires Python 3.10+.

## Reading EFT files

`cadeft.Reader` reads a whole file and returns a `cadeft.File`:

```python
import cadeft

with open("eft_file.txt", encoding="utf-8") as f:
    eft_file = cadeft.Reader(f).read_file()

for txn in eft_file.txns:
    print(txn)
```

`cadeft.FileStreamer` yields one transaction per `scan_txn()` call, so parse errors can be handled per
transaction. `EndOfFile` signals completion and `ParseError` a malformed record:

```python
with open("eft_file.txt", encoding="utf-8") as f:
    streamer = cadeft.FileStreamer(f)
    while True:
        try:
            txn = streamer.scan_txn()
        except cadeft.EndOfFile:
            break
        except cadeft.ParseError as err:
            print("parse error:", err)
            continue
        txn.validate()
```

`FileStreamer` keeps parser state and is not thread-safe; call `scan_txn()` from a single thread and hand
the resulting transactions to workers.

## Writing EFT files

```python
from datetime import date
import cadeft

header = cadeft.NewFileHeader("123456789", 1, date.today(), 610, "CAD")
txn = cadeft.NewCredit(
    "450", 420, date.today(), "123456789", "12345", "222222222222222",
    "payor name", "payee name", "payor long name", "987654321", "54321",
    cadeft.WithCrossRefNo("0000100000024"),
)

eft_file = cadeft.NewFile(header, [txn])
eft_file.validate()  # raises cadeft.ValidationError if the file breaks the 005 spec
print(eft_file.create())
```

## 80-byte (TD) format

`cadeft.eft80` reads and writes the 80-byte A/X/C/D/Y/Z record format. Trailing blanks may be trimmed on
input, non-spec characters are rejected unless `strict=False` (also on `validate` and `create`), records may be delimited by CR, LF or CRLF, and batch controls and the footer are computed on output:

```python
from cadeft.eft80 import parse_file80

eft_file = parse_file80(open("td_fees.txt").read())
eft_file.validate()
print(eft_file.create(eol="\r"))
```

## Command line

```sh
python -m cadeft --mode parse --file eft_file.txt            # EFT -> JSON
python -m cadeft --mode build --validate --file input.json   # JSON -> EFT
```

## Testing

```sh
python -m unittest discover -s tests
```

`tests/test_parity.py` checks this package against the original Go implementation at a pinned upstream
commit (`GO_REFERENCE_COMMIT`), which it clones automatically; it needs `git` and `go` and is skipped
otherwise. Set `CADEFT_GO_REF_COMMIT` to test another upstream commit.

CI (`upstream-parity.yml`) replays **every** upstream `moov-io/cadeft` commit quarterly (and on pull requests and manual dispatch),
and publishes a pass/fail table per commit in the run summary. The release workflow attaches that report
to each GitHub release.

## Releasing

Update the version in `pyproject.toml` and `CHANGELOG.md`, then push a tag (`git tag v0.2.0 && git push origin v0.2.0`).
The release workflow runs the parity suite, builds the package, creates the GitHub release, and publishes
to PyPI via [trusted publishing](https://docs.pypi.org/trusted-publishers/) (configure the `pypi` environment).

## Project status

cadeft is currently being developed for use in multiple production environments. The library was developed in large part by [Synctera](https://synctera.com/). Please star the project if you are interested in its progress. Let us know if you encounter any bugs/unclear documentation or have feature suggestions by opening up an issue or pull request. Thanks!

## Attribution

This project is a derivative work of [moov-io/cadeft](https://github.com/moov-io/cadeft),
Copyright 2023 Moov.IO, licensed under the Apache License 2.0. The Go sources were re-implemented in
Python. See [NOTICE](NOTICE).

## License

Apache License 2.0 - See [LICENSE](LICENSE) for details.
