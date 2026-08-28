from __future__ import annotations

import pytest

from cryptobox.preview import content_media_type, preview_kind


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("photo.JPEG", "image"),
        ("photo.HEIC", "image"),
        ("alternate.HeIf", "image"),
        ("diagram.svg", "svg"),
        ("movie.mp4", "video"),
        ("voice.opus", "audio"),
        ("report.PDF", "pdf"),
        ("README.md", "markdown"),
        ("records.CSV", "table"),
        ("page.html", "html"),
        ("script.py", "text"),
        ("Dockerfile", "unknown"),
        ("report.docx", "word"),
        ("model.xlsm", "spreadsheet"),
        ("slides.ppsx", "presentation"),
        ("book.epub", "ebook"),
        ("backup.zip", "archive"),
        ("legacy.doc", "unsupported"),
        ("legacy.xls", "unsupported"),
        ("legacy.ppt", "unsupported"),
        ("program.exe", "unsupported"),
    ],
)
def test_preview_kind_is_stable_across_common_extensions(name: str, expected: str) -> None:
    assert preview_kind(name) == expected


def test_active_content_is_always_served_as_plain_text() -> None:
    assert content_media_type("page.html") == "text/plain; charset=utf-8"
    assert content_media_type("image.svg") == "text/plain; charset=utf-8"
    assert content_media_type("module.mjs") == "text/plain; charset=utf-8"


def test_office_and_pdf_media_types_do_not_depend_on_platform_registry() -> None:
    assert content_media_type("report.pdf") == "application/pdf"
    assert content_media_type("report.docx") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert content_media_type("sheet.xlsx") == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


def test_heic_media_types_are_explicit_and_case_insensitive() -> None:
    assert content_media_type("photo.HEIC") == "image/heic"
    assert content_media_type("alternate.HeIf") == "image/heif"
