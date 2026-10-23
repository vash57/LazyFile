"""Scanning, rule-based organization, preview generation, apply and undo."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import secrets
import stat
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

import safety
from config import CATEGORIES, EXTENSION_MAP, MULTI_SUFFIXES


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _file_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in MULTI_SUFFIXES:
        if lower.endswith(suffix):
            return suffix
    return Path(name).suffix.lower()


_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...], str], ...] = (
    (
        "Finance",
        (
            "invoice",
            "receipt",
            "bank statement",
            "statement",
            "tax return",
            "taxes",
            "paystub",
            "payslip",
            "budget",
            "expense",
        ),
        "Finance-related filename",
    ),
    (
        "Images",
        ("screenshot", "screen shot", "screen capture", "snip"),
        "Screenshot or capture",
    ),
    (
        "Archives",
        ("backup", "archive", "bundle"),
        "Backup or archive name",
    ),
    (
        "Data",
        ("dataset", "data export", "database dump", "dump"),
        "Data-related filename",
    ),
    (
        "Documents",
        (
            "resume",
            "curriculum vitae",
            "cover letter",
            "assignment",
            "lecture notes",
            "syllabus",
            "homework",
            "worksheet",
        ),
        "Document-related filename",
    ),
)


def _keyword_reason(stem: str) -> tuple[str, str] | None:
    normalized = re.sub(r"[^a-z0-9]+", " ", stem.lower()).strip()
    if not normalized:
        return None
    for category, phrases, label in _KEYWORD_RULES:
        for phrase in phrases:
            pattern = rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z])"
            if re.search(pattern, normalized):
                return category, label
    return None


def categorize_file(name: str) -> tuple[str, str]:
    """Return ``(category, reason)`` using extension and filename rules."""
    suffix = _file_suffix(name)
    stem = name[: -len(suffix)] if suffix else name
    keyword = _keyword_reason(stem)
    if keyword:
        category, label = keyword
        return category, f"{label} matched a built-in rule."

    if suffix:
        category = EXTENSION_MAP.get(suffix)
        if category:
            return category, f"{suffix.upper()} files belong with {category}."
        return "Other", f"{suffix.upper()} files have no matching category rule."

    return "Other", "The file has no extension, so it was placed in Other."


@dataclass(frozen=True)
class FileRecord:
    id: str
    rel_path: str
    name: str
    suffix: str
    size: int
    mtime_ns: int
    category: str
    category_reason: str

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rel_path": self.rel_path,
            "name": self.name,
            "suffix": self.suffix,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
            "category": self.category,
            "category_reason": self.category_reason,
        }


@dataclass
class ScanResult:
    root: Path
    files: list[FileRecord]
    directories_scanned: int
    warnings: list[str]
    truncated: bool
    skipped_symlinks: int


def _warning(warnings: list[str], message: str, limit: int = 50) -> None:
    if len(warnings) < limit:
        warnings.append(message)
    elif len(warnings) == limit:
        warnings.append("Additional warnings were omitted.")


def scan_directory(
    root: str | os.PathLike[str],
    max_files: int = 3000,
    max_depth: int = 16,
) -> ScanResult:
    """Scan only the selected directory, ignoring hidden entries and symlinks."""
    root_path = Path(os.path.abspath(root))
    files: list[FileRecord] = []
    warnings: list[str] = []
    directories_scanned = 0
    skipped_symlinks = 0
    truncated = False
    depth_warned = False
    stack: list[tuple[Path, int]] = [(root_path, 0)]

    while stack and not truncated:
        directory, depth = stack.pop()
        directories_scanned += 1
        try:
            with os.scandir(directory) as entries:
                for entry in entries:
                    try:
                        if safety.is_hidden_name(entry.name):
                            continue
                        if entry.is_symlink():
                            skipped_symlinks += 1
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if depth >= max_depth:
                                if not depth_warned:
                                    _warning(
                                        warnings,
                                        f"Maximum folder depth ({max_depth}) reached; "
                                        "deeper folders were skipped.",
                                    )
                                    depth_warned = True
                                continue
                            stack.append((Path(entry.path), depth + 1))
                            continue
                        if not entry.is_file(follow_symlinks=False):
                            continue
                        if len(files) >= max_files:
                            truncated = True
                            break

                        info = entry.stat(follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode):
                            continue

                        relative = os.path.relpath(entry.path, root_path)
                        relative = relative.replace(os.sep, "/")
                        suffix = _file_suffix(entry.name)
                        category, category_reason = categorize_file(entry.name)
                        digest = hashlib.sha1(
                            relative.encode("utf-8", "surrogateescape")
                        ).hexdigest()[:14]
                        files.append(
                            FileRecord(
                                id=f"f_{digest}",
                                rel_path=relative,
                                name=entry.name,
                                suffix=suffix,
                                size=int(info.st_size),
                                mtime_ns=int(info.st_mtime_ns),
                                category=category,
                                category_reason=category_reason,
                            )
                        )
                    except PermissionError:
                        _warning(
                            warnings,
                            f"Permission denied while reading: {entry.name}",
                        )
                    except OSError as exc:
                        _warning(
                            warnings,
                            f"Could not inspect {entry.name}: {exc.strerror or exc}",
                        )
        except PermissionError:
            _warning(
                warnings,
                f"Permission denied while scanning: {directory.name or directory}",
            )
        except OSError as exc:
            _warning(
                warnings,
                f"Could not scan {directory.name or directory}: {exc.strerror or exc}",
            )

    files.sort(key=lambda item: item.rel_path.casefold())
    if truncated:
        _warning(
            warnings,
            f"Scan stopped at the configured limit of {max_files} files.",
        )

    return ScanResult(
        root=root_path,
        files=files,
        directories_scanned=directories_scanned,
        warnings=warnings,
        truncated=truncated,
        skipped_symlinks=skipped_symlinks,
    )


@dataclass
class Suggestion:
    id: str
    file_id: str
    original_rel: str
    original_name: str
    proposed_name: str
    category: str
    destination_rel: str
    action: str
    reason: str
    source: str
    changed: bool
    conflict: bool
    conflict_reason: str
    status: str
    size: int
    mtime_ns: int

    @property
    def can_apply(self) -> bool:
        return self.changed and not self.conflict and self.status == "proposed"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_id": self.file_id,
            "original_rel": self.original_rel,
            "original_name": self.original_name,
            "proposed_name": self.proposed_name,
            "category": self.category,
            "destination_rel": self.destination_rel,
            "action": self.action,
            "reason": self.reason,
            "source": self.source,
            "changed": self.changed,
            "conflict": self.conflict,
            "conflict_reason": self.conflict_reason,
            "can_apply": self.can_apply,
            "status": self.status,
            "size": self.size,
            "mtime_ns": self.mtime_ns,
        }


def _clean_reason(value: object, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return fallback
    return text[:240]


def _build_reason(
    record: FileRecord,
    category: str,
    name_changed: bool,
    folder_changed: bool,
    ai_reason: str | None,
) -> str:
    if not name_changed and not folder_changed:
        return "Already organized. No change is needed."
    if ai_reason:
        return ai_reason

    parts = [record.category_reason]
    if folder_changed:
        parts.append(f"Grouped into {category}.")
    if name_changed:
        parts.append("Filename cleaned for consistency.")
    return " ".join(parts)


def _suggestion_id(file_id: str, category: str, name: str) -> str:
    digest = hashlib.sha1(
        f"{file_id}|{category}|{name}".encode("utf-8", "surrogateescape")
    ).hexdigest()[:14]
    return f"s_{digest}"


def _destination_key(root: Path, category: str, name: str) -> str:
    raw = str(root / category / name)
    return os.path.normcase(raw).casefold()


def build_suggestions(
    records: list[FileRecord],
    root: str | os.PathLike[str],
    ai_overrides: dict[str, dict] | None = None,
) -> list[Suggestion]:
    """Build a conservative preview. This function never modifies the filesystem."""
    root_path = Path(os.path.abspath(root))
    overrides = ai_overrides or {}
    suggestions: list[Suggestion] = []

    for record in records:
        category = record.category
        proposed_name = safety.sanitize_filename(record.name, fallback="file")
        source = "rules"
        ai_reason: str | None = None

        override = overrides.get(record.id)
        if isinstance(override, dict):
            override_category = override.get("category")
            if isinstance(override_category, str):
                match = next(
                    (
                        item
                        for item in CATEGORIES
                        if item.casefold() == override_category.strip().casefold()
                    ),
                    None,
                )
                if match:
                    category = match
                    source = "ai"

            override_name = override.get("filename")
            if isinstance(override_name, str) and override_name.strip():
                candidate = override_name.strip()
                if "/" not in candidate and "\\" not in candidate:
                    candidate = safety.sanitize_filename(candidate, fallback=record.name)
                    original_suffix = Path(record.name).suffix
                    candidate_suffix = Path(candidate).suffix
                    suffix_ok = (
                        not original_suffix
                        or candidate_suffix.casefold() == original_suffix.casefold()
                    )
                    try:
                        safety.validate_filename(candidate)
                    except safety.SafetyError:
                        suffix_ok = False
                    if suffix_ok:
                        proposed_name = candidate
                        source = "ai"

            if source == "ai":
                ai_reason = _clean_reason(
                    override.get("reason"),
                    f"Local AI grouped this file as {category}.",
                )

        current_parent = str(PurePosixPath(record.rel_path).parent)
        if current_parent == ".":
            current_parent = ""
        folder_changed = current_parent != category
        name_changed = proposed_name != record.name

        if folder_changed and name_changed:
            action = "move_rename"
        elif folder_changed:
            action = "move"
        elif name_changed:
            action = "rename"
        else:
            action = "none"

        destination_rel = f"{category}/{proposed_name}"
        suggestions.append(
            Suggestion(
                id=_suggestion_id(record.id, category, proposed_name),
                file_id=record.id,
                original_rel=record.rel_path,
                original_name=record.name,
                proposed_name=proposed_name,
                category=category,
                destination_rel=destination_rel,
                action=action,
                reason=_build_reason(
                    record, category, name_changed, folder_changed, ai_reason
                ),
                source=source,
                changed=action != "none",
                conflict=False,
                conflict_reason="",
                status="proposed",
                size=record.size,
                mtime_ns=record.mtime_ns,
            )
        )

    changed = [item for item in suggestions if item.changed]
    destination_groups: dict[str, list[Suggestion]] = {}
    for suggestion in changed:
        key = _destination_key(
            root_path, suggestion.category, suggestion.proposed_name
        )
        destination_groups.setdefault(key, []).append(suggestion)

    for group in destination_groups.values():
        if len(group) > 1:
            for suggestion in group:
                suggestion.conflict = True
                suggestion.conflict_reason = (
                    "Another proposed file has the same destination."
                )

    for suggestion in changed:
        destination = root_path / suggestion.category / suggestion.proposed_name
        try:
            if safety.has_symlink_component(root_path, destination):
                suggestion.conflict = True
                suggestion.conflict_reason = (
                    "The destination path contains a symbolic link."
                )
                continue
        except (OSError, safety.SafetyError):
            suggestion.conflict = True
            suggestion.conflict_reason = "The destination path is not safe."
            continue

        try:
            if os.path.lexists(destination):
                suggestion.conflict = True
                suggestion.conflict_reason = "A file already exists at that destination."
            elif destination.parent.exists() and not destination.parent.is_dir():
                suggestion.conflict = True
                suggestion.conflict_reason = "The destination parent is not a folder."
        except OSError:
            suggestion.conflict = True
            suggestion.conflict_reason = "The destination could not be inspected."

    return suggestions


def _result(
    suggestion: Suggestion, status: str, message: str
) -> dict[str, str]:
    return {
        "id": suggestion.id,
        "original_rel": suggestion.original_rel,
        "destination_rel": suggestion.destination_rel,
        "status": status,
        "message": message,
    }


def apply_suggestions(
    root: str | os.PathLike[str],
    suggestions: list[Suggestion],
    selected_ids: list[str] | set[str],
    *,
    dry_run: bool = True,
    store: "OperationStore | None" = None,
) -> dict:
    """Apply only explicitly selected suggestions.

    Dry-run is the default. No file is deleted. Each move is checked again at
    apply time, including the source fingerprint and destination collision.
    """
    root_path = Path(os.path.abspath(root))
    selected = set(selected_ids)
    chosen = [item for item in suggestions if item.id in selected]
    actionable = [
        item for item in chosen if item.can_apply
    ]

    operation_id: str | None = None
    if not dry_run and actionable and store is not None:
        operation_id = store.start(root_path, planned=len(actionable))

    results: list[dict[str, str]] = []
    moved = 0
    skipped = 0
    failed = 0

    for suggestion in chosen:
        if suggestion.status == "applied":
            results.append(
                _result(suggestion, "skipped", "This file was already moved.")
            )
            skipped += 1
            continue
        if not suggestion.changed:
            results.append(
                _result(suggestion, "skipped", "No change is needed for this file.")
            )
            skipped += 1
            continue
        if suggestion.conflict:
            results.append(
                _result(
                    suggestion,
                    "skipped",
                    suggestion.conflict_reason or "The destination is not safe.",
                )
            )
            skipped += 1
            continue

        source: Path | None = None
        destination: Path | None = None
        try:
            source = safety.safe_join(
                root_path, *suggestion.original_rel.split("/")
            )
            destination = safety.safe_join(
                root_path, suggestion.category, suggestion.proposed_name
            )
            safety.assert_no_symlink_components(root_path, source)
            safety.assert_no_symlink_components(root_path, destination)

            if not os.path.lexists(source) or source.is_symlink():
                results.append(
                    _result(
                        suggestion,
                        "skipped",
                        "The source file is missing or is a symbolic link.",
                    )
                )
                skipped += 1
                continue
            current = safety.file_fingerprint(source)
            if (
                current["size"] != suggestion.size
                or current["mtime_ns"] != suggestion.mtime_ns
            ):
                results.append(
                    _result(
                        suggestion,
                        "skipped",
                        "The file changed after the preview was generated.",
                    )
                )
                skipped += 1
                continue

            if os.path.lexists(destination):
                results.append(
                    _result(
                        suggestion,
                        "skipped",
                        "A file appeared at the destination after preview.",
                    )
                )
                skipped += 1
                continue
            if destination.parent.exists() and not destination.parent.is_dir():
                results.append(
                    _result(
                        suggestion,
                        "skipped",
                        "The destination parent is not a folder.",
                    )
                )
                skipped += 1
                continue

            if dry_run:
                results.append(
                    _result(
                        suggestion,
                        "would_move",
                        f"Would move to {suggestion.destination_rel}.",
                    )
                )
                moved += 1
                continue

            safety.ensure_safe_directory(root_path, destination.parent)
            if os.path.lexists(destination):
                results.append(
                    _result(
                        suggestion,
                        "skipped",
                        "A file appeared at the destination after preview.",
                    )
                )
                skipped += 1
                continue

            safety.safe_move(root_path, source, destination)
            suggestion.status = "applied"
            moved += 1
            results.append(
                _result(
                    suggestion,
                    "moved",
                    f"Moved to {suggestion.destination_rel}.",
                )
            )
            if operation_id and store is not None:
                placed = safety.file_fingerprint(destination)
                store.record_item(
                    operation_id,
                    src=source,
                    dst=destination,
                    size=placed["size"],
                    mtime_ns=placed["mtime_ns"],
                )
        except FileExistsError:
            results.append(
                _result(
                    suggestion,
                    "skipped",
                    "The destination already exists.",
                )
            )
            skipped += 1
        except (OSError, safety.SafetyError) as exc:
            results.append(
                _result(suggestion, "failed", _clean_reason(str(exc), "Move failed."))
            )
            failed += 1

    if operation_id and store is not None:
        if moved == 0:
            status = "failed"
        elif failed:
            status = "partial"
        else:
            status = "completed"
        store.finish(operation_id, status=status, moved=moved, failed=failed)

    return {
        "dry_run": bool(dry_run),
        "operation_id": operation_id,
        "results": results,
        "summary": {
            "selected": len(chosen),
            "moved": moved,
            "would_move": moved if dry_run else 0,
            "skipped": skipped,
            "failed": failed,
        },
    }


class OperationStore:
    """Small append-only JSONL journal for apply and undo operations."""

    def __init__(self, data_dir: str | os.PathLike[str]):
        self.data_dir = Path(data_dir)
        self.path = self.data_dir / "operations.jsonl"
        self.durable = True
        self.error = ""
        self._lock = threading.RLock()
        self._operations: OrderedDict[str, dict] = OrderedDict()
        self._handle = None
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(self.data_dir, 0o700)
            except OSError:
                pass
            self._handle = self.path.open("a", encoding="utf-8", buffering=1)
            self._load()
        except OSError as exc:
            self.durable = False
            self.error = f"Operation log unavailable: {exc}"
            self._handle = None

    def _load(self) -> None:
        if not self.path.is_file():
            return
        try:
            with self.path.open("r", encoding="utf-8", errors="replace") as handle:
                for raw in handle:
                    if len(raw) > 1_000_000:
                        continue
                    try:
                        event = json.loads(raw)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if isinstance(event, dict):
                        self._apply_event(event)
        except OSError as exc:
            self.durable = False
            self.error = f"Could not read operation log: {exc}"
        for operation in self._operations.values():
            if operation.get("status") == "in_progress":
                operation["status"] = "interrupted"

    def _apply_event(self, event: dict) -> None:
        event_type = event.get("type")
        op_id = event.get("op_id")
        if not isinstance(op_id, str):
            return

        if event_type == "start":
            self._operations[op_id] = {
                "id": op_id,
                "root": str(event.get("root", "")),
                "started_at": event.get("at", ""),
                "status": "in_progress",
                "planned": int(event.get("planned", 0) or 0),
                "items": [],
                "finished_at": "",
                "undo_items": [],
            }
            return

        operation = self._operations.get(op_id)
        if operation is None:
            return

        if event_type == "item":
            operation["items"].append(
                {
                    "src": str(event.get("src", "")),
                    "dst": str(event.get("dst", "")),
                    "size": int(event.get("size", 0) or 0),
                    "mtime_ns": int(event.get("mtime_ns", 0) or 0),
                    "at": event.get("at", ""),
                }
            )
        elif event_type == "finish":
            operation["status"] = str(event.get("status", "completed"))
            operation["finished_at"] = event.get("at", "")
        elif event_type == "undo_item":
            operation["undo_items"].append(
                {
                    "src": str(event.get("src", "")),
                    "dst": str(event.get("dst", "")),
                    "status": str(event.get("status", "")),
                    "message": str(event.get("message", "")),
                    "at": event.get("at", ""),
                }
            )
        elif event_type == "undo_finish":
            operation["status"] = str(event.get("status", "undone"))
            operation["finished_at"] = event.get("at", "")

    def _append(self, event: dict) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.write(
                        json.dumps(event, ensure_ascii=False, separators=(",", ":"))
                        + "\n"
                    )
                    self._handle.flush()
                except OSError as exc:
                    self.durable = False
                    self.error = f"Could not write operation log: {exc}"
            event_with_at = dict(event)
            event_with_at.setdefault("at", _utc_now())
            self._apply_event(event_with_at)

    def start(self, root: str | os.PathLike[str], planned: int) -> str:
        operation_id = f"op_{int(time.time())}_{secrets.token_hex(4)}"
        self._append(
            {
                "type": "start",
                "op_id": operation_id,
                "root": str(root),
                "planned": int(planned),
                "at": _utc_now(),
            }
        )
        return operation_id

    def record_item(
        self,
        operation_id: str,
        *,
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        size: int,
        mtime_ns: int,
    ) -> None:
        self._append(
            {
                "type": "item",
                "op_id": operation_id,
                "src": str(src),
                "dst": str(dst),
                "size": int(size),
                "mtime_ns": int(mtime_ns),
                "at": _utc_now(),
            }
        )

    def finish(
        self, operation_id: str, *, status: str, moved: int, failed: int
    ) -> None:
        self._append(
            {
                "type": "finish",
                "op_id": operation_id,
                "status": status,
                "moved": int(moved),
                "failed": int(failed),
                "at": _utc_now(),
            }
        )

    def record_undo_item(
        self,
        operation_id: str,
        *,
        src: str | os.PathLike[str],
        dst: str | os.PathLike[str],
        status: str,
        message: str,
    ) -> None:
        self._append(
            {
                "type": "undo_item",
                "op_id": operation_id,
                "src": str(src),
                "dst": str(dst),
                "status": status,
                "message": message,
                "at": _utc_now(),
            }
        )

    def finish_undo(self, operation_id: str, status: str) -> None:
        self._append(
            {
                "type": "undo_finish",
                "op_id": operation_id,
                "status": status,
                "at": _utc_now(),
            }
        )

    def get(self, operation_id: str) -> dict | None:
        with self._lock:
            operation = self._operations.get(operation_id)
            return copy.deepcopy(operation) if operation else None

    def recent(self, limit: int = 20) -> list[dict]:
        with self._lock:
            operations = list(self._operations.values())[-limit:]
        operations.reverse()
        result = []
        for operation in operations:
            status = operation.get("status", "unknown")
            undoable = (
                bool(operation.get("items"))
                and status
                in ("completed", "partial", "interrupted", "partially_undone", "undo_failed")
            )
            result.append(
                {
                    "id": operation["id"],
                    "root": operation.get("root", ""),
                    "started_at": operation.get("started_at", ""),
                    "finished_at": operation.get("finished_at", ""),
                    "status": status,
                    "planned": operation.get("planned", 0),
                    "moved": len(operation.get("items", [])),
                    "undoable": undoable,
                    "items": copy.deepcopy(operation.get("items", [])),
                    "undo_items": copy.deepcopy(operation.get("undo_items", [])),
                }
            )
        return result

    def close(self) -> None:
        with self._lock:
            if self._handle is not None:
                try:
                    self._handle.close()
                except OSError:
                    pass
                self._handle = None


def undo_operation(store: OperationStore, operation_id: str) -> dict:
    """Move files from a completed operation back where they came from."""
    operation = store.get(operation_id)
    if not operation:
        raise safety.SafetyError("That operation was not found.")
    if operation.get("status") == "undone":
        raise safety.SafetyError("That operation has already been undone.")
    items = operation.get("items") or []
    if not items:
        raise safety.SafetyError("That operation has no completed moves to undo.")

    root = Path(operation.get("root", ""))
    try:
        if root.is_symlink() or not root.is_dir():
            raise safety.SafetyError("The original folder is no longer available.")
    except OSError as exc:
        raise safety.SafetyError(f"The original folder is unavailable: {exc}") from exc

    results: list[dict[str, str]] = []
    restored = 0
    already = 0
    blocked = 0
    failed = 0

    for item in reversed(items):
        src = Path(item.get("src", ""))
        dst = Path(item.get("dst", ""))
        try:
            safety.assert_within_root(root, src)
            safety.assert_within_root(root, dst)
            safety.assert_no_symlink_components(root, src)
            safety.assert_no_symlink_components(root, dst)

            if not os.path.lexists(dst):
                if os.path.lexists(src) and not src.is_symlink():
                    message = "The file is already back at its original path."
                    store.record_undo_item(
                        operation_id,
                        src=src,
                        dst=dst,
                        status="already_restored",
                        message=message,
                    )
                    results.append(
                        {
                            "src": str(src),
                            "dst": str(dst),
                            "status": "already_restored",
                            "message": message,
                        }
                    )
                    already += 1
                else:
                    message = "The moved file is no longer at its destination."
                    store.record_undo_item(
                        operation_id,
                        src=src,
                        dst=dst,
                        status="blocked",
                        message=message,
                    )
                    results.append(
                        {
                            "src": str(src),
                            "dst": str(dst),
                            "status": "blocked",
                            "message": message,
                        }
                    )
                    blocked += 1
                continue

            if os.path.lexists(src):
                message = "The original path is already occupied."
                store.record_undo_item(
                    operation_id,
                    src=src,
                    dst=dst,
                    status="blocked",
                    message=message,
                )
                results.append(
                    {
                        "src": str(src),
                        "dst": str(dst),
                        "status": "blocked",
                        "message": message,
                    }
                )
                blocked += 1
                continue

            expected = {
                "size": int(item.get("size", 0) or 0),
                "mtime_ns": int(item.get("mtime_ns", 0) or 0),
            }
            if not safety.fingerprint_matches(dst, expected):
                message = "The file changed after it was moved, so undo was skipped."
                store.record_undo_item(
                    operation_id,
                    src=src,
                    dst=dst,
                    status="blocked",
                    message=message,
                )
                results.append(
                    {
                        "src": str(src),
                        "dst": str(dst),
                        "status": "blocked",
                        "message": message,
                    }
                )
                blocked += 1
                continue

            safety.ensure_safe_directory(root, src.parent)
            safety.safe_move(root, dst, src)
            store.record_undo_item(
                operation_id,
                src=src,
                dst=dst,
                status="restored",
                message="Restored to the original path.",
            )
            results.append(
                {
                    "src": str(src),
                    "dst": str(dst),
                    "status": "restored",
                    "message": "Restored to the original path.",
                }
            )
            restored += 1
        except (OSError, safety.SafetyError) as exc:
            message = _clean_reason(str(exc), "Undo failed.")
            try:
                store.record_undo_item(
                    operation_id,
                    src=src,
                    dst=dst,
                    status="failed",
                    message=message,
                )
            except Exception:
                pass
            results.append(
                {
                    "src": str(src),
                    "dst": str(dst),
                    "status": "failed",
                    "message": message,
                }
            )
            failed += 1

    if blocked == 0 and failed == 0:
        status = "undone"
    elif restored or already:
        status = "partially_undone"
    else:
        status = "undo_failed"
    store.finish_undo(operation_id, status)

    return {
        "operation_id": operation_id,
        "status": status,
        "results": results,
        "summary": {
            "restored": restored,
            "already_restored": already,
            "blocked": blocked,
            "failed": failed,
            "total": len(items),
        },
    }
