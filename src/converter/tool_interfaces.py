from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import PageData, PDFInfo, RenderedPage, StructureResult, TextExtractionResult


class PDFInspector(Protocol):
    def inspect(self, pdf: Path) -> PDFInfo: ...


class PageRenderer(Protocol):
    def render(self, pdf: Path, destination: Path, info: PDFInfo) -> list[RenderedPage]: ...


class NativeTextExtractor(Protocol):
    def extract(self, pdf: Path, info: PDFInfo) -> TextExtractionResult: ...


class OCREngine(Protocol):
    def recognize(self, pages: Sequence[RenderedPage]) -> TextExtractionResult: ...


class SemanticParser(Protocol):
    def parse(self, pdf: Path, pages: list[PageData]) -> StructureResult: ...


@dataclass(frozen=True)
class ConversionTools:
    """Static composition root for conversion tool implementations."""

    inspector: PDFInspector
    renderer: PageRenderer
    native_text: NativeTextExtractor
    ocr: OCREngine
    semantic_parser: SemanticParser
