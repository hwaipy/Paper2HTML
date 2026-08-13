from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


@dataclass(frozen=True)
class EngineInfo:
    """Identity recorded for one extraction implementation."""

    name: str
    version: str


@dataclass(frozen=True)
class PDFInfo:
    """Page geometry required by all PDF tools."""

    page_count: int
    sizes: list[tuple[float, float]]
    rotations: list[int]


@dataclass(frozen=True)
class TextBox:
    page: int
    text: str
    bbox: tuple[float, float, float, float]
    font_size: float = 0.0
    confidence: float = 1.0


@dataclass(frozen=True)
class RenderedPage:
    number: int
    image_path: Path
    width_px: int
    height_px: int
    dpi: int = 300


PageStatus = Literal["completed", "no-text", "failed"]


@dataclass(frozen=True)
class TextExtractionResult:
    engine: EngineInfo
    pages: list[list[TextBox]]
    page_statuses: list[PageStatus]
    warnings: list[str] = field(default_factory=list)


@dataclass
class ContentBlock:
    page: int
    text: str
    boxes: list[TextBox]
    kind: str = "paragraph"
    element_id: str = ""
    child_ids: list[str] = field(default_factory=list)
    caption_boxes: list[TextBox] = field(default_factory=list)
    resources: list[dict[str, Any]] = field(default_factory=list)
    revision_before: str | None = None
    aff_rids: list[str] = field(default_factory=list)
    equal_contribution: bool = False
    derived_bbox: tuple[float, float, float, float] | None = None

    @property
    def regions(self) -> list[list[float]]:
        return [list(box.bbox) for box in self.boxes]


@dataclass
class PageData:
    number: int
    width_pt: float
    height_pt: float
    rotation: int
    image_path: Path
    image_width: int
    image_height: int
    native: list[TextBox] = field(default_factory=list)
    ocr: list[TextBox] = field(default_factory=list)


@dataclass
class FrontMatter:
    authors: list[ContentBlock] = field(default_factory=list)
    affiliations: list[ContentBlock] = field(default_factory=list)
    abstract: ContentBlock | None = None
    publication_date: ContentBlock | None = None
    contribution_note: ContentBlock | None = None


@dataclass
class StructureResult:
    title: ContentBlock
    front: FrontMatter
    blocks: list[ContentBlock]
    omissions: list[dict[str, Any]]
