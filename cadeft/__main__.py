"""Command-line interface compatible with the Go cadeft utility."""

from __future__ import annotations

import argparse
import json
import sys

from . import File, Reader, file_from_dict


def main() -> int:
    parser = argparse.ArgumentParser(prog="cadeft")
    parser.add_argument("--mode", required=True, choices=("parse", "build"))
    parser.add_argument("--file", default="")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    try:
        if args.mode == "parse":
            if args.file:
                with open(args.file, encoding="utf-8") as source:
                    result = Reader(source).read_file()
            else:
                result = Reader(sys.stdin.buffer.read()).read_file()
            sys.stdout.write(json.dumps(result.to_dict(), ensure_ascii=False, separators=(",", ":")))
            return 0
        if args.file:
            with open(args.file, encoding="utf-8") as source:
                eft_file = file_from_dict(json.load(source))
        else:
            eft_file = file_from_dict(json.load(sys.stdin))
        if args.validate:
            eft_file.validate()
        sys.stdout.write(eft_file.create())
        return 0
    except Exception as exc:
        print(f"cadeft: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
