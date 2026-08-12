from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

CheckStatus = Literal["passed", "failed", "partial", "not-run", "not-applicable"]

CHECKS = (
    "manifest_schema",
    "xml_well_formed",
    "jats_bits_schema",
    "p2h_profile",
    "id_uniqueness",
    "cross_references",
    "page_coverage",
    "element_provenance",
    "asset_integrity",
    "checksum_integrity",
)


@dataclass(frozen=True)
class Finding:
    code: str
    message: str
    path: str | None = None
    line: int | None = None
    element_id: str | None = None
    source_id: str | None = None
    physical_page: int | None = None

    def as_dict(self) -> dict[str, Any]:
        result = {key: value for key, value in vars(self).items() if value is not None}
        path = result.get("path")
        if isinstance(path, str) and not re.fullmatch(
            r"(?!/)(?!.*(?:^|/)\.\.(?:/|$))(?!.*//)[a-z0-9._-]+(?:/[a-z0-9._-]+)*", path
        ):
            result["x-invalid-path"] = result.pop("path")
        return result


@dataclass
class State:
    checks: dict[str, CheckStatus] = field(default_factory=lambda: dict.fromkeys(CHECKS, "not-run"))
    errors: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)
    statistics: dict[str, int] = field(default_factory=dict)
    operational_error: bool = False

    def error(self, check: str, code: str, message: str, **context: Any) -> None:
        self.errors.append(Finding(code, message, **context))
        self.checks[check] = "failed"

    def warning(self, code: str, message: str, **context: Any) -> None:
        self.warnings.append(Finding(code, message, **context))

    def pass_if_not_failed(self, check: str) -> None:
        if self.checks[check] != "failed":
            self.checks[check] = "passed"
