from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .pipeline import ConversionError, ConversionOptions, convert_pdf


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m src.converter.cli",
        description="Convert one born-digital PDF into a minimal P2H Package 0.1 directory",
    )
    parser.add_argument("input", type=Path, help="local PDF or paper2html-pdf-source descriptor")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--replace", action="store_true", help="atomically replace an existing output directory"
    )
    parser.add_argument("--created-at", help="fixed RFC 3339 UTC completion time for reproducible tests")
    parser.add_argument("--cache-dir", type=Path, help="validator resource cache")
    parser.add_argument("--download-cache-dir", type=Path, help="verified remote-PDF cache")
    parser.add_argument(
        "--secure-dns",
        action="store_true",
        help="resolve remote hosts through pinned TLS DNS when local DNS uses synthetic addresses",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="download a descriptor PDF and missing locked validation resources",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        report = convert_pdf(
            args.input,
            args.output,
            ConversionOptions(
                created_at=args.created_at,
                replace=args.replace,
                allow_network=args.allow_network,
                cache_dir=args.cache_dir,
                download_cache_dir=args.download_cache_dir,
                secure_dns=args.secure_dns,
            ),
        )
    except ConversionError as exc:
        print(f"conversion failed: {exc}", file=sys.stderr)
        return 2
    if args.json_output:
        json.dump(report, sys.stdout, ensure_ascii=False, indent=2)
        print()
    else:
        print(f"wrote {args.output}")
        print(f"validation: {'VALID' if report['valid'] else 'INVALID'}")
        print(f"errors={len(report['errors'])} warnings={len(report['warnings'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
