from __future__ import annotations

import json
import unicodedata
from pathlib import Path
from typing import Any

from .models import State


def inspect_text(path: Path, package_path: str, state: State, check: str) -> str | None:
    try:
        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            state.error(check, "text_bom", "Text files must not contain a UTF-8 BOM.", path=package_path)
        if b"\r" in raw:
            state.error(check, "text_line_endings", "Text files must use LF line endings.", path=package_path)
        text = raw.decode("utf-8")
        if unicodedata.normalize("NFC", text) != text:
            state.error(
                check, "text_not_nfc", "Text content is not Unicode NFC-normalized.", path=package_path
            )
        return text.removeprefix("\ufeff")
    except UnicodeDecodeError as exc:
        state.error(check, "text_invalid_utf8", f"Invalid UTF-8: {exc}", path=package_path)
    except OSError as exc:
        state.error(check, "file_read_error", f"Cannot read file: {exc}", path=package_path)
    return None


def load_json(path: Path, package_path: str, state: State, check: str) -> Any | None:
    text = inspect_text(path, package_path, state, check)
    if text is None:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        state.error(check, "json_syntax", exc.msg, path=package_path, line=exc.lineno)
        return None


def load_jsonl(path: Path, package_path: str, state: State, check: str) -> list[tuple[int, Any]]:
    text = inspect_text(path, package_path, state, check)
    if text is None:
        return []
    records: list[tuple[int, Any]] = []
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            records.append((number, json.loads(line)))
        except json.JSONDecodeError as exc:
            state.error(check, "jsonl_syntax", exc.msg, path=package_path, line=number)
    return records
