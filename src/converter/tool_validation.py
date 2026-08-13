from __future__ import annotations

from pathlib import Path

from .models import PDFInfo, RenderedPage, TextExtractionResult


class ToolContractError(ValueError):
    """Raised when a tool returns data outside the converter contract."""


def validate_pdf_info(info: PDFInfo) -> PDFInfo:
    if info.page_count <= 0:
        raise ToolContractError("PDF inspector returned a non-positive page count")
    if len(info.sizes) != info.page_count or len(info.rotations) != info.page_count:
        raise ToolContractError("PDF inspector page geometry does not match its page count")
    if any(width <= 0 or height <= 0 for width, height in info.sizes):
        raise ToolContractError("PDF inspector returned a non-positive page size")
    if any(rotation not in {0, 90, 180, 270} for rotation in info.rotations):
        raise ToolContractError("PDF inspector returned an unsupported page rotation")
    return info


def validate_rendered_pages(
    pages: list[RenderedPage], info: PDFInfo, destination: Path
) -> list[RenderedPage]:
    if len(pages) != info.page_count:
        raise ToolContractError(
            f"page renderer returned {len(pages)} pages; expected {info.page_count}"
        )
    root = destination.resolve()
    for expected, page in enumerate(pages, 1):
        if page.number != expected:
            raise ToolContractError("page renderer returned non-contiguous physical page numbers")
        if page.dpi != 300:
            raise ToolContractError("canonical evidence pages must be rendered at 300 DPI")
        if page.width_px <= 0 or page.height_px <= 0:
            raise ToolContractError(f"page renderer returned an invalid image size for page {expected}")
        image_path = page.image_path.resolve()
        if image_path != root and root not in image_path.parents:
            raise ToolContractError("page renderer wrote an image outside its destination")
        if not image_path.is_file():
            raise ToolContractError(f"page renderer did not create the image for page {expected}")
    return pages


def validate_text_extraction(
    result: TextExtractionResult, info: PDFInfo, *, role: str
) -> TextExtractionResult:
    if not result.engine.name.strip() or not result.engine.version.strip():
        raise ToolContractError(f"{role} did not identify its engine name and version")
    if len(result.pages) != info.page_count or len(result.page_statuses) != info.page_count:
        raise ToolContractError(f"{role} page results do not match the PDF page count")
    for page_number, (boxes, status) in enumerate(zip(result.pages, result.page_statuses, strict=True), 1):
        if status == "failed":
            raise ToolContractError(f"{role} failed on page {page_number}")
        if status == "completed" and not boxes:
            raise ToolContractError(f"{role} marked empty page {page_number} as completed")
        if status == "no-text" and boxes:
            raise ToolContractError(f"{role} marked non-empty page {page_number} as no-text")
        for box in boxes:
            if box.page != page_number:
                raise ToolContractError(f"{role} returned a text box under the wrong page")
            x0, y0, x1, y1 = box.bbox
            if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
                raise ToolContractError(f"{role} returned an invalid normalized bbox")
            if not 0 <= box.confidence <= 1:
                raise ToolContractError(f"{role} returned confidence outside [0, 1]")
    return result
