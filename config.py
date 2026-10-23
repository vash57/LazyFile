"""Configuration and shared constants for LazyFile.

LazyFile intentionally has no third-party dependencies. Environment variables
are read from the process and, optionally, from a local ``.env`` file.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
STATIC_DIR = PROJECT_ROOT / "static"
VERSION = "1.0.0"

CATEGORIES = [
    "Documents",
    "Finance",
    "Spreadsheets",
    "Presentations",
    "Images",
    "Video",
    "Audio",
    "Archives",
    "Code",
    "Data",
    "Ebooks",
    "Executables",
    "Fonts",
    "Other",
]

CATEGORY_INFO = {
    "Documents": {
        "label": "Documents",
        "description": "PDFs, text files, and word processing documents",
    },
    "Finance": {
        "label": "Finance",
        "description": "Invoices, receipts, statements, tax files, and budgets",
    },
    "Spreadsheets": {
        "label": "Spreadsheets",
        "description": "Workbooks and tabular exports",
    },
    "Presentations": {
        "label": "Presentations",
        "description": "Slide decks and presentation files",
    },
    "Images": {
        "label": "Images",
        "description": "Photos, screenshots, and graphics",
    },
    "Video": {
        "label": "Video",
        "description": "Clips, recordings, and films",
    },
    "Audio": {
        "label": "Audio",
        "description": "Music, voice notes, and sound files",
    },
    "Archives": {
        "label": "Archives",
        "description": "Compressed folders, packages, and backups",
    },
    "Code": {
        "label": "Code",
        "description": "Source files, scripts, and web assets",
    },
    "Data": {
        "label": "Data",
        "description": "Structured data, configuration, and databases",
    },
    "Ebooks": {
        "label": "Ebooks",
        "description": "Electronic books and long-form reading",
    },
    "Executables": {
        "label": "Executables",
        "description": "Installers and application packages",
    },
    "Fonts": {
        "label": "Fonts",
        "description": "Typeface and web font files",
    },
    "Other": {
        "label": "Other",
        "description": "Files that do not match a known category",
    },
}

EXTENSION_MAP = {
    # Documents
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".rtf": "Documents",
    ".odt": "Documents",
    ".md": "Documents",
    ".markdown": "Documents",
    ".tex": "Documents",
    ".pages": "Documents",
    ".epub": "Ebooks",
    ".mobi": "Ebooks",
    ".azw": "Ebooks",
    ".azw3": "Ebooks",
    ".fb2": "Ebooks",
    ".djvu": "Ebooks",
    # Spreadsheets
    ".xls": "Spreadsheets",
    ".xlsx": "Spreadsheets",
    ".xlsm": "Spreadsheets",
    ".ods": "Spreadsheets",
    ".numbers": "Spreadsheets",
    ".csv": "Spreadsheets",
    ".tsv": "Spreadsheets",
    # Presentations
    ".ppt": "Presentations",
    ".pptx": "Presentations",
    ".odp": "Presentations",
    ".key": "Presentations",
    # Images
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".webp": "Images",
    ".svg": "Images",
    ".bmp": "Images",
    ".tif": "Images",
    ".tiff": "Images",
    ".heic": "Images",
    ".heif": "Images",
    ".avif": "Images",
    ".ico": "Images",
    ".raw": "Images",
    ".psd": "Images",
    ".ai": "Images",
    ".sketch": "Images",
    # Video
    ".mp4": "Video",
    ".mkv": "Video",
    ".mov": "Video",
    ".avi": "Video",
    ".webm": "Video",
    ".m4v": "Video",
    ".wmv": "Video",
    ".flv": "Video",
    # Audio
    ".mp3": "Audio",
    ".wav": "Audio",
    ".flac": "Audio",
    ".m4a": "Audio",
    ".aac": "Audio",
    ".ogg": "Audio",
    ".opus": "Audio",
    ".wma": "Audio",
    ".aiff": "Audio",
    # Archives
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".bz2": "Archives",
    ".xz": "Archives",
    ".tgz": "Archives",
    ".iso": "Archives",
    # Code
    ".py": "Code",
    ".pyw": "Code",
    ".js": "Code",
    ".mjs": "Code",
    ".cjs": "Code",
    ".ts": "Code",
    ".tsx": "Code",
    ".jsx": "Code",
    ".java": "Code",
    ".c": "Code",
    ".h": "Code",
    ".cpp": "Code",
    ".hpp": "Code",
    ".cc": "Code",
    ".cs": "Code",
    ".go": "Code",
    ".rs": "Code",
    ".rb": "Code",
    ".php": "Code",
    ".swift": "Code",
    ".kt": "Code",
    ".kts": "Code",
    ".scala": "Code",
    ".lua": "Code",
    ".r": "Code",
    ".sh": "Code",
    ".bash": "Code",
    ".zsh": "Code",
    ".ps1": "Code",
    ".html": "Code",
    ".htm": "Code",
    ".css": "Code",
    ".scss": "Code",
    ".sass": "Code",
    ".less": "Code",
    ".vue": "Code",
    ".svelte": "Code",
    # Data
    ".json": "Data",
    ".jsonl": "Data",
    ".xml": "Data",
    ".yaml": "Data",
    ".yml": "Data",
    ".toml": "Data",
    ".ini": "Data",
    ".cfg": "Data",
    ".conf": "Data",
    ".sql": "Data",
    ".db": "Data",
    ".sqlite": "Data",
    ".sqlite3": "Data",
    ".parquet": "Data",
    ".log": "Data",
    # Executables
    ".exe": "Executables",
    ".msi": "Executables",
    ".apk": "Executables",
    ".appimage": "Executables",
    ".deb": "Executables",
    ".rpm": "Executables",
    ".dmg": "Executables",
    # Fonts
    ".ttf": "Fonts",
    ".otf": "Fonts",
    ".woff": "Fonts",
    ".woff2": "Fonts",
    ".eot": "Fonts",
}

MULTI_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")

_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _load_dotenv(path: Path) -> None:
    """Load simple KEY=VALUE pairs without overriding real environment vars."""
    try:
        if not path.is_file():
            return
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not _ENV_KEY.match(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        # Real environment variables always win over the file.
        os.environ.setdefault(key, value)


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ollama_timeout: float = 60.0
    max_scan_files: int = 3000
    max_scan_depth: int = 16
    ai_max_files: int = 80
    data_dir: Path = Path.home() / ".lazyfile"

    @classmethod
    def from_env(cls) -> "Settings":
        _load_dotenv(PROJECT_ROOT / ".env")
        data_dir_raw = os.environ.get("LAZYFILE_DATA_DIR", "").strip()
        data_dir = (
            Path(data_dir_raw).expanduser()
            if data_dir_raw
            else Path.home() / ".lazyfile"
        )
        return cls(
            host=os.environ.get("LAZYFILE_HOST", "127.0.0.1").strip()
            or "127.0.0.1",
            port=_env_int("LAZYFILE_PORT", 8765, 0, 65535),
            ollama_url=os.environ.get(
                "OLLAMA_URL", "http://127.0.0.1:11434"
            ).strip()
            or "http://127.0.0.1:11434",
            ollama_model=os.environ.get("OLLAMA_MODEL", "").strip(),
            ollama_timeout=_env_float("OLLAMA_TIMEOUT", 60.0, 3.0, 600.0),
            max_scan_files=_env_int("LAZYFILE_MAX_FILES", 3000, 50, 20000),
            max_scan_depth=_env_int("LAZYFILE_MAX_DEPTH", 16, 1, 64),
            ai_max_files=_env_int("LAZYFILE_AI_MAX_FILES", 80, 1, 300),
            data_dir=data_dir,
        )


def default_downloads_path() -> str:
    """Return an existing Downloads folder when possible, otherwise home."""
    home = Path.home()
    downloads = home / "Downloads"
    try:
        if downloads.is_dir() and not downloads.is_symlink():
            return str(downloads)
    except OSError:
        pass
    return str(home)
