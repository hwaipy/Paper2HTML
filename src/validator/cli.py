from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .validator import ValidationOptions, validate_package, write_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.validator.cli", description="Validate a P2H Package 0.1 directory"
    )
    parser.add_argument("package", type=Path, help="package root containing manifest.json")
    parser.add_argument(
        "--write-report",
        action="store_true",
        help="atomically replace validation/report.json and repair its checksum",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="write the independently computed report to stdout",
    )
    parser.add_argument(
        "--coverage-evidence", type=Path, help="independent visible-object audit evidence JSON"
    )
    parser.add_argument("--schema-dir", type=Path, help="normative schema/0.1 directory")
    parser.add_argument("--cache-dir", type=Path, help="verified upstream-schema cache")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="download missing locked schemas; hashes are always verified",
    )
    parser.add_argument(
        "--katex-command",
        help="command reading TeX from stdin and returning nonzero when it is invalid",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    options = ValidationOptions(
        schema_dir=args.schema_dir,
        cache_dir=args.cache_dir,
        allow_network=args.allow_network,
        coverage_evidence=args.coverage_evidence,
        katex_command=args.katex_command,
    )
    result = validate_package(args.package, options, writing_report=args.write_report)
    if args.write_report and not result.operational_error:
        write_report(args.package, result)
    if args.json_output:
        json.dump(result.report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        status = "VALID" if result.report["valid"] else "INVALID"
        print(f"P2H Package 0.1: {status}")
        for name, value in result.report["checks"].items():
            print(f"  {name:22} {value}")
        print(f"  errors={len(result.report['errors'])} warnings={len(result.report['warnings'])}")
        for finding in result.report["errors"][:20]:
            location = f" [{finding['path']}]" if "path" in finding else ""
            print(f"  ERROR {finding['code']}{location}: {finding['message']}")
        if len(result.report["errors"]) > 20:
            print(f"  ... {len(result.report['errors']) - 20} more errors (use --json)")
        if args.write_report and not result.operational_error:
            print("  wrote validation/report.json and updated its checksums.sha256 entry")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
