"""Extraction for uploaded PDFs and Markdown files.

PDF: ``pypdf`` text extraction. If the extracted text is essentially
empty (image-only / scanned PDFs without an OCR layer), raise a
``ScannedPdfError`` so the API layer can return a structured 422 instead
of silently passing an empty requirement into the pipeline.

Markdown: pass-through. ``pypdf`` is unsuited to ``.md``, but the same
parser surface is convenient for the upload endpoint.

Bounds:

* ``MAX_INPUT_CHARS`` caps the extracted requirement size before it
  reaches the pipeline. This mirrors the artifact-side bound enforced by
  ``agents/_sanitize.py``. A 1 MB extracted requirement would blow past
  the eval/cost budgets in Phase 13.1 and is almost certainly an upload
  mistake - fail fast.
* The PDF size check is at the byte level so a 50 MB PDF never gets fed
  to ``pypdf`` in the first place.
"""

from __future__ import annotations

import io

from pypdf import PdfReader

# 200 KB of text ≈ 50–60 pages of dense requirements. Generous for a
# real PRD, tight enough to catch a runaway upload.
MAX_INPUT_CHARS = 200_000

# 10 MB max PDF - picked to mirror the text cap above with headroom for
# image content that won't contribute to the extracted text anyway.
MAX_PDF_BYTES = 10 * 1024 * 1024

# A "scanned" PDF has effectively no extracted text. Pick a small floor
# rather than zero so a PDF with a stray page number doesn't sneak past.
_SCANNED_THRESHOLD_CHARS = 50


class ParseError(ValueError):
    """Base class for upload parse failures. Maps to HTTP 422 at the API layer."""


class ScannedPdfError(ParseError):
    """Raised when a PDF has no extractable text layer (image-only / scanned)."""


class FileTooLargeError(ParseError):
    """Raised when the uploaded file exceeds ``MAX_PDF_BYTES`` / char cap."""


def extract_pdf(data: bytes) -> str:
    """Extract text from a PDF byte string.

    Raises ``FileTooLargeError`` if the upload exceeds ``MAX_PDF_BYTES``.
    Raises ``ScannedPdfError`` if extraction yields effectively no text.
    Raises ``ParseError`` for any malformed-PDF case (wraps ``pypdf``'s
    errors so the API never leaks library-specific exception types).
    """
    if len(data) > MAX_PDF_BYTES:
        raise FileTooLargeError(f"PDF too large: {len(data)} bytes (max {MAX_PDF_BYTES}).")
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as exc:  # pypdf raises a handful of types; treat as one class
        raise ParseError(f"Failed to parse PDF: {exc}") from exc

    text = "\n\n".join(p.strip() for p in pages if p.strip())
    if len(text) < _SCANNED_THRESHOLD_CHARS:
        raise ScannedPdfError(
            "PDF appears to be scanned or image-only (no extractable text). "
            "OCR is not supported in v1 - re-upload a text-based PDF."
        )
    return _cap(text)


def extract_markdown(data: bytes) -> str:
    """UTF-8 decode + cap. No structural parsing - markdown passes through.

    Uses ``utf-8-sig`` so a leading byte-order mark (U+FEFF) - common on
    Markdown files saved by Windows editors or exported from Notion - is
    stripped instead of surviving into the analyst input as a stray
    character. Files without a BOM are decoded identically to plain
    UTF-8. Epic #1 intake-soundness audit (#1).
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ParseError(f"Markdown file is not UTF-8: {exc}") from exc
    return _cap(text)


def _cap(text: str) -> str:
    """Hard-cap extracted text to ``MAX_INPUT_CHARS``.

    Truncation is preferred over rejection: a too-long PRD usually has
    its load-bearing requirements at the top, and an explicit truncation
    notice tells the operator something was dropped.
    """
    if len(text) <= MAX_INPUT_CHARS:
        return text
    notice = (
        f"\n\n[TRUNCATED - input exceeded {MAX_INPUT_CHARS} characters; "
        "tail dropped before extraction reached the pipeline.]"
    )
    return text[: MAX_INPUT_CHARS - len(notice)] + notice


__all__ = [
    "FileTooLargeError",
    "MAX_INPUT_CHARS",
    "MAX_PDF_BYTES",
    "ParseError",
    "ScannedPdfError",
    "extract_markdown",
    "extract_pdf",
]
