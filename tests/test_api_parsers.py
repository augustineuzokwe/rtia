"""Tests for PDF + Markdown extraction helpers."""

from __future__ import annotations

import io

import pytest
from pypdf import PdfWriter

from api.parsers import (
    MAX_INPUT_CHARS,
    MAX_PDF_BYTES,
    FileTooLargeError,
    ParseError,
    ScannedPdfError,
    extract_markdown,
    extract_pdf,
)


def _make_text_pdf(text: str) -> bytes:
    """Build a tiny PDF whose first page contains `text`.

    Uses reportlab if available; otherwise constructs a PDF by hand via
    pypdf's writer + a manual content stream. Keeps the test offline.
    """
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab not installed — needed only for PDF fixture generation")

    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    # Break the string into 80-char lines to fit a page.
    y = 800
    for line in [text[i : i + 80] for i in range(0, len(text), 80)]:
        c.drawString(50, y, line)
        y -= 14
    c.save()
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    """A blank PDF with no text layer — simulates a scanned/image-only PDF."""
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


def test_extract_markdown_roundtrip():
    text = "# Requirement\n\nAs a user, I want X."
    assert extract_markdown(text.encode("utf-8")) == text


def test_extract_markdown_rejects_non_utf8():
    with pytest.raises(ParseError):
        extract_markdown(b"\xff\xfe\x00binary garbage")


def test_extract_markdown_strips_utf8_bom():
    """Epic #1 intake-soundness audit (#1) — Markdown files saved by
    Windows editors or exported from Notion frequently carry a leading
    UTF-8 BOM (``\\xef\\xbb\\xbf``). Pre-fix, ``data.decode("utf-8")``
    accepted the BOM but left U+FEFF at the start of the text, which
    then surfaced in the Analyst's input as an invisible artifact.
    Switching to ``utf-8-sig`` strips it transparently while still
    handling BOM-less files identically."""
    bom = b"\xef\xbb\xbf"
    text = "# Requirement\n\nAs a user, I want X."
    extracted = extract_markdown(bom + text.encode("utf-8"))
    assert extracted == text
    assert not extracted.startswith("﻿")


def test_extract_markdown_bomless_unchanged():
    """The BOM strip must not affect BOM-less files — their bytes are
    canonical UTF-8 and round-trip exactly."""
    text = "# No BOM here\n\nplain ascii."
    assert extract_markdown(text.encode("utf-8")) == text


def test_extract_pdf_blank_pdf_is_scanned():
    with pytest.raises(ScannedPdfError):
        extract_pdf(_make_blank_pdf())


def test_extract_pdf_too_large():
    # Construct a byte string slightly larger than the cap without
    # actually generating a real PDF — the size check runs first.
    too_big = b"%PDF-" + b"x" * (MAX_PDF_BYTES + 1)
    with pytest.raises(FileTooLargeError):
        extract_pdf(too_big)


def test_extract_pdf_malformed_wraps_parse_error():
    with pytest.raises(ParseError):
        extract_pdf(b"not a pdf at all")


def test_extract_pdf_with_text_content():
    # Skips if reportlab is not installed (CI installs it for this test).
    sample = "RTIA requirement — as a user, I want to upload a PDF and see it parsed."
    pdf_bytes = _make_text_pdf(sample)
    extracted = extract_pdf(pdf_bytes)
    # pypdf's extraction is approximate; key tokens should survive.
    assert "RTIA" in extracted
    assert "PDF" in extracted


def test_extract_markdown_truncates_oversize():
    blob = ("x" * (MAX_INPUT_CHARS + 1000)).encode("utf-8")
    out = extract_markdown(blob)
    assert len(out) <= MAX_INPUT_CHARS
    assert "TRUNCATED" in out
