from __future__ import annotations

from pathlib import Path

import pytest

from src.converter.models import EngineInfo, PDFInfo, RenderedPage, TextBox, TextExtractionResult
from src.converter.tool_validation import (
    ToolContractError,
    validate_rendered_pages,
    validate_text_extraction,
)


def test_text_tool_contract_rejects_invalid_normalized_bbox() -> None:
    info = PDFInfo(1, [(612.0, 792.0)], [0])
    result = TextExtractionResult(
        EngineInfo("test-ocr", "1"),
        [[TextBox(1, "outside", (-0.1, 0.1, 0.5, 0.2))]],
        ["completed"],
    )
    with pytest.raises(ToolContractError, match="normalized bbox"):
        validate_text_extraction(result, info, role="OCR engine")


def test_renderer_contract_rejects_noncanonical_dpi(tmp_path: Path) -> None:
    image = tmp_path / "page-000001.png"
    image.write_bytes(b"not-inspected-by-contract")
    info = PDFInfo(1, [(612.0, 792.0)], [0])
    with pytest.raises(ToolContractError, match="300 DPI"):
        validate_rendered_pages([RenderedPage(1, image, 612, 792, 144)], info, tmp_path)
