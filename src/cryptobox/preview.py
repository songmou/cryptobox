from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Literal

PreviewKind = Literal[
    "image",
    "svg",
    "video",
    "audio",
    "pdf",
    "text",
    "html",
    "markdown",
    "table",
    "word",
    "spreadsheet",
    "presentation",
    "ebook",
    "archive",
    "unknown",
    "unsupported",
]

_PREVIEW_EXTENSIONS: dict[PreviewKind, frozenset[str]] = {
    "image": frozenset({".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".avif"}),
    "svg": frozenset({".svg"}),
    "video": frozenset({".mp4", ".webm", ".mov", ".m4v", ".ogv"}),
    "audio": frozenset({".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus"}),
    "pdf": frozenset({".pdf"}),
    "markdown": frozenset({".md", ".markdown", ".mdown"}),
    "table": frozenset({".csv", ".tsv"}),
    "html": frozenset({".html", ".htm", ".xhtml"}),
    "word": frozenset({".docx", ".docm", ".dotx"}),
    "spreadsheet": frozenset({".xlsx", ".xlsm", ".xlsb", ".ods"}),
    "presentation": frozenset({".pptx", ".pptm", ".ppsx"}),
    "ebook": frozenset({".epub"}),
    "archive": frozenset({".zip"}),
    "text": frozenset(
        {
            ".txt", ".log", ".json", ".jsonl", ".xml", ".yaml", ".yml", ".toml", ".ini",
            ".cfg", ".conf", ".env", ".properties", ".py", ".pyw", ".js", ".mjs", ".cjs",
            ".ts", ".tsx", ".jsx", ".css", ".scss", ".less", ".sql", ".sh", ".bash", ".zsh",
            ".fish", ".ps1", ".bat", ".cmd", ".java", ".kt", ".kts", ".c", ".h", ".cc", ".cpp",
            ".cxx", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".swift", ".dart", ".lua",
            ".r", ".tex", ".gitignore", ".gitattributes", ".editorconfig",
        }
    ),
    "unsupported": frozenset({".doc", ".xls", ".ppt"}),
}

_EXTENSION_KIND = {
    extension: kind for kind, extensions in _PREVIEW_EXTENSIONS.items() for extension in extensions
}

_MEDIA_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".csv": "text/csv; charset=utf-8",
    ".docm": "application/vnd.ms-word.document.macroEnabled.12",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".dotx": "application/vnd.openxmlformats-officedocument.wordprocessingml.template",
    ".epub": "application/epub+zip",
    ".flac": "audio/flac",
    ".gif": "image/gif",
    ".ico": "image/x-icon",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".m4a": "audio/mp4",
    ".markdown": "text/markdown; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
    ".mov": "video/quicktime",
    ".mp3": "audio/mpeg",
    ".mp4": "video/mp4",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".ogg": "audio/ogg",
    ".ogv": "video/ogg",
    ".opus": "audio/ogg",
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".ppsx": "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
    ".pptm": "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".svg": "image/svg+xml",
    ".tsv": "text/tab-separated-values; charset=utf-8",
    ".wav": "audio/wav",
    ".webm": "video/webm",
    ".webp": "image/webp",
    ".xlsb": "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".zip": "application/zip",
}

_PLAIN_TEXT_RESPONSE_EXTENSIONS = {
    ".html", ".htm", ".xhtml", ".svg", ".xml", ".js", ".mjs", ".cjs",
}


def preview_kind(name: str) -> PreviewKind:
    path = Path(name)
    suffix = path.suffix.lower()
    if not suffix:
        return "unknown"
    return _EXTENSION_KIND.get(suffix, "unsupported")


def content_media_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in _PLAIN_TEXT_RESPONSE_EXTENSIONS:
        return "text/plain; charset=utf-8"
    explicit = _MEDIA_TYPES.get(suffix)
    if explicit:
        return explicit
    guessed = mimetypes.guess_type(name)[0]
    return guessed or "application/octet-stream"

