"""Filesystem safety primitives for LazyFile.

Every path that LazyFile reads or writes goes through this module. The rules are
deliberately conservative:

* only regular files are organized
* symbolic links are never followed
* destinations must stay inside the selected root
* existing files are never overwritten
* moves use an atomic hard-link first, with a no-overwrite copy fallback
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterable

MAX_FILENAME_BYTES = 255

_INVALID_CHARS = set('<>:"/\\|?*')
_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    "COM1",
    "COM2",
    "COM3",
    "COM4",
    "COM5",
    "COM6",
    "COM7",
    "COM8",
    "COM9",
    "LPT1",
    "LPT2",
    "LPT3",
    "LPT4",
    "LPT5",
    "LPT6",
    "LPT7",
    "LPT8",
    "LPT9",
    "CONIN$",
    "CONOUT$",
}
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_WHITESPACE_RE = re.compile(r"\s+")


class SafetyError(ValueError):
    """Raised when an operation would be unsafe."""


def is_hidden_name(name: str) -> bool:
    return isinstance(name, str) and name.startswith(".") and name not in (".", "..")


def _truncate_filename(name: str, limit: int = MAX_FILENAME_BYTES) -> str:
    encoded = name.encode("utf-8")
    if len(encoded) <= limit:
        return name

    suffix = Path(name).suffix
    suffix_bytes = suffix.encode("utf-8")
    if len(suffix_bytes) > 64:
        suffix = ""
        suffix_bytes = b""

    stem = name[: -len(suffix)] if suffix else name
    budget = max(1, limit - len(suffix_bytes))
    stem = stem.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(" .")
    if not stem:
        stem = "file"
    return stem + suffix


def _is_reserved(name: str) -> bool:
    base = name.split(".", 1)[0].strip().rstrip(".").upper()
    return (
        base in _RESERVED_NAMES
        or re.fullmatch(r"COM[1-9]", base) is not None
        or re.fullmatch(r"LPT[1-9]", base) is not None
    )


def sanitize_filename(name: object, fallback: str = "file") -> str:
    """Return a safe single filename component.

    This is intentionally more forgiving than :func:`validate_filename` so that
    model output can be repaired before it is validated. It never returns a path.
    """
    if not isinstance(name, str):
        name = fallback

    name = unicodedata.normalize("NFC", name)
    name = _CONTROL_RE.sub("", name)
    name = name.replace("/", "_").replace("\\", "_")
    for char in _INVALID_CHARS:
        name = name.replace(char, "_")
    name = _WHITESPACE_RE.sub(" ", name)
    name = name.strip(" .")

    while name.startswith("."):
        name = name[1:].lstrip()
    name = name.strip(" .")

    if not name:
        name = fallback if isinstance(fallback, str) else "file"
        name = _CONTROL_RE.sub("", name)
        name = name.replace("/", "_").replace("\\", "_")
        for char in _INVALID_CHARS:
            name = name.replace(char, "_")
        name = _WHITESPACE_RE.sub(" ", name).strip(" .")
        while name.startswith("."):
            name = name[1:].lstrip()
        name = name.strip(" .")
        if not name:
            name = "file"

    name = _truncate_filename(name)
    if _is_reserved(name):
        name = "_" + name
        name = _truncate_filename(name)

    try:
        validate_filename(name)
    except SafetyError:
        name = _truncate_filename("file")
    return name


def validate_filename(name: object) -> str:
    """Validate a single filename component, raising :class:`SafetyError`."""
    if not isinstance(name, str) or not name:
        raise SafetyError("Filename must be a non-empty string.")
    if name in (".", ".."):
        raise SafetyError("Filename may not be '.' or '..'.")
    if len(name.encode("utf-8")) > MAX_FILENAME_BYTES:
        raise SafetyError("Filename is too long.")
    if "\x00" in name or _CONTROL_RE.search(name):
        raise SafetyError("Filename contains control characters.")
    if "/" in name or "\\" in name or (os.altsep and os.altsep in name):
        raise SafetyError("Filename may not contain path separators.")
    if name.startswith("."):
        raise SafetyError("Destination filenames may not be hidden.")
    if name.endswith(" ") or name.endswith("."):
        raise SafetyError("Filename may not end with a space or period.")
    if any(char in _INVALID_CHARS for char in name):
        raise SafetyError("Filename contains characters that are not portable.")
    if _is_reserved(name):
        raise SafetyError("Filename is reserved by the operating system.")
    return name


def _absolute(path: str | os.PathLike[str]) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_selected_directory(raw_path: object) -> Path:
    """Validate the directory explicitly chosen by the user."""
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise SafetyError("Enter a folder path to scan.")
    if len(raw_path) > 4096:
        raise SafetyError("The folder path is too long.")
    if "\x00" in raw_path:
        raise SafetyError("The folder path contains a null byte.")

    expanded = os.path.expanduser(raw_path.strip())
    path = _absolute(expanded)

    try:
        if path.is_symlink():
            raise SafetyError("Select a real folder, not a symbolic link.")
        st = path.lstat()
    except FileNotFoundError as exc:
        raise SafetyError("That folder does not exist.") from exc
    except PermissionError as exc:
        raise SafetyError("That folder cannot be accessed.") from exc
    except OSError as exc:
        raise SafetyError(f"Could not inspect that folder: {exc}") from exc

    if not stat.S_ISDIR(st.st_mode):
        raise SafetyError("The selected path is not a directory.")
    if path.parent == path:
        raise SafetyError("Scanning a filesystem root is not allowed.")
    if not os.access(path, os.R_OK | os.X_OK):
        raise SafetyError("That folder is not readable.")

    return path


def assert_within_root(
    root: str | os.PathLike[str],
    target: str | os.PathLike[str],
    *,
    allow_root: bool = False,
) -> None:
    """Ensure a target is lexically and physically inside the scan root."""
    root_abs = _absolute(root)
    target_abs = _absolute(target)
    root_real = Path(os.path.realpath(root_abs))
    target_real = Path(os.path.realpath(target_abs))

    if not allow_root and target_abs == root_abs:
        raise SafetyError("The operation may not target the scan root itself.")
    if not _is_relative_to(target_abs, root_abs):
        raise SafetyError("Path escapes the selected folder.")
    if not _is_relative_to(target_real, root_real):
        raise SafetyError("Path resolves outside the selected folder.")
    if target_abs == root_abs and allow_root and root_abs.is_symlink():
        raise SafetyError("The selected folder may not be a symbolic link.")


def has_symlink_component(
    root: str | os.PathLike[str], target: str | os.PathLike[str]
) -> bool:
    try:
        assert_no_symlink_components(root, target)
        return False
    except SafetyError:
        return True


def assert_no_symlink_components(
    root: str | os.PathLike[str], target: str | os.PathLike[str]
) -> None:
    """Reject symbolic links anywhere at or below the scan root."""
    root_abs = _absolute(root)
    target_abs = _absolute(target)
    if target_abs != root_abs and not _is_relative_to(target_abs, root_abs):
        raise SafetyError("Path escapes the selected folder.")

    if root_abs.is_symlink():
        raise SafetyError("The selected folder may not be a symbolic link.")

    relative = target_abs.relative_to(root_abs)
    current = root_abs
    for part in relative.parts:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            return
        except OSError as exc:
            raise SafetyError(f"Could not inspect path component: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise SafetyError("Symbolic links are not followed.")


def safe_join(
    root: str | os.PathLike[str], *parts: str | os.PathLike[str]
) -> Path:
    """Join path components while guaranteeing the result stays under root."""
    root_path = _absolute(root)
    result = root_path
    for part in parts:
        text = os.fspath(part)
        if not isinstance(text, str):
            raise SafetyError("Path components must be text.")
        if text in ("", ".", ".."):
            raise SafetyError("Invalid path component.")
        if "\x00" in text or "/" in text or "\\" in text:
            raise SafetyError("Path component may not contain separators.")
        result = result / text
    assert_within_root(root_path, result)
    return result


def ensure_safe_directory(
    root: str | os.PathLike[str], directory: str | os.PathLike[str]
) -> Path:
    """Create a directory below root without traversing symbolic links."""
    root_path = _absolute(root)
    directory_path = _absolute(directory)
    assert_within_root(root_path, directory_path, allow_root=True)
    assert_no_symlink_components(root_path, directory_path)

    try:
        if directory_path.exists():
            if not directory_path.is_dir():
                raise SafetyError("A path component is not a directory.")
            return directory_path
    except OSError as exc:
        raise SafetyError(f"Could not inspect destination: {exc}") from exc

    relative = directory_path.relative_to(root_path)
    current = root_path
    for part in relative.parts:
        current = current / part
        try:
            st = current.lstat()
        except FileNotFoundError:
            try:
                os.mkdir(current, 0o755)
            except FileExistsError:
                pass
            except OSError as exc:
                raise SafetyError(f"Could not create destination folder: {exc}") from exc
            continue
        except OSError as exc:
            raise SafetyError(f"Could not inspect destination: {exc}") from exc
        if stat.S_ISLNK(st.st_mode):
            raise SafetyError("Destination contains a symbolic link.")
        if not stat.S_ISDIR(st.st_mode):
            raise SafetyError("A destination component is not a directory.")

    return directory_path


def file_fingerprint(path: str | os.PathLike[str]) -> dict[str, int]:
    """Return cheap metadata used to verify a file has not changed."""
    try:
        st = os.lstat(os.fspath(path))
    except OSError as exc:
        raise SafetyError(f"Could not read file metadata: {exc}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise SafetyError("Only regular files can be organized.")
    return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}


def fingerprint_matches(
    path: str | os.PathLike[str], expected: dict[str, int] | None
) -> bool:
    if not expected:
        return False
    try:
        current = file_fingerprint(path)
    except SafetyError:
        return False
    return (
        current.get("size") == expected.get("size")
        and current.get("mtime_ns") == expected.get("mtime_ns")
    )


def _unlink_quiet(path: str | os.PathLike[str]) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _link_no_follow(src: Path, dst: Path) -> None:
    try:
        os.link(src, dst, follow_symlinks=False)
    except (NotImplementedError, TypeError):
        os.link(src, dst)


def _copy_move(src: Path, dst: Path, parent: Path) -> str:
    """No-overwrite copy fallback for filesystems without hard-link support."""
    fd, temp_name = tempfile.mkstemp(
        prefix=".lazyfile-tmp-", suffix=".part", dir=str(parent)
    )
    temp_path = Path(temp_name)
    created_destination = False
    try:
        with os.fdopen(fd, "wb") as out, open(src, "rb") as source:
            shutil.copyfileobj(source, out, length=1024 * 1024)
            out.flush()
            os.fsync(out.fileno())
        try:
            shutil.copystat(src, temp_path, follow_symlinks=False)
        except OSError:
            pass

        if os.path.lexists(dst):
            raise FileExistsError(f"Destination already exists: {dst}")

        try:
            _link_no_follow(temp_path, dst)
        except OSError:
            # Last resort: exclusive creation. It still cannot overwrite.
            if os.path.lexists(dst):
                raise FileExistsError(f"Destination already exists: {dst}")
            try:
                with open(dst, "xb") as out, open(temp_path, "rb") as source:
                    created_destination = True
                    shutil.copyfileobj(source, out, length=1024 * 1024)
                    out.flush()
                    os.fsync(out.fileno())
                try:
                    shutil.copystat(temp_path, dst, follow_symlinks=False)
                except OSError:
                    pass
            except Exception:
                if created_destination:
                    _unlink_quiet(dst)
                raise

        try:
            os.unlink(src)
        except OSError:
            # Roll back the new destination; the original remains untouched.
            _unlink_quiet(dst)
            raise
        return "copy"
    finally:
        _unlink_quiet(temp_path)


def safe_move(
    root: str | os.PathLike[str],
    src: str | os.PathLike[str],
    dst: str | os.PathLike[str],
) -> str:
    """Move a regular file without ever overwriting the destination.

    Returns ``"link"`` or ``"copy"`` describing the mechanism used. The hard-link
    path is atomic on POSIX: it either creates the destination completely or
    fails, and removing the source afterwards completes the move.
    """
    root_path = _absolute(root)
    src_path = _absolute(src)
    dst_path = _absolute(dst)

    assert_within_root(root_path, src_path)
    assert_within_root(root_path, dst_path)
    assert_no_symlink_components(root_path, src_path)
    assert_no_symlink_components(root_path, dst_path)

    try:
        src_stat = src_path.lstat()
    except OSError as exc:
        raise SafetyError(f"Source file is unavailable: {exc}") from exc
    if stat.S_ISLNK(src_stat.st_mode) or not stat.S_ISREG(src_stat.st_mode):
        raise SafetyError("Only regular, non-symlink files can be moved.")
    if not dst_path.parent.is_dir():
        raise SafetyError("The destination folder does not exist.")
    if os.path.lexists(dst_path):
        raise FileExistsError(f"Destination already exists: {dst_path.name}")

    try:
        _link_no_follow(src_path, dst_path)
    except FileExistsError:
        raise
    except OSError:
        return _copy_move(src_path, dst_path, dst_path.parent)

    try:
        os.unlink(src_path)
    except OSError:
        _unlink_quiet(dst_path)
        raise
    return "link"


def unique_names(candidates: Iterable[str]) -> dict[str, int]:
    """Return case-insensitive duplicate counts for proposed filenames."""
    counts: dict[str, int] = {}
    for candidate in candidates:
        key = unicodedata.normalize("NFC", candidate).casefold()
        counts[key] = counts.get(key, 0) + 1
    return counts
