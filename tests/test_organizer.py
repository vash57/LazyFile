from __future__ import annotations

import dataclasses
import os
import tempfile
import unittest
from pathlib import Path

import organizer


class OrganizerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()
        self.data_dir = self.base / "data"

    def write(self, relative: str, content: str = "data") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def scan_and_build(self) -> tuple[list[organizer.Suggestion], organizer.ScanResult]:
        result = organizer.scan_directory(self.root)
        suggestions = organizer.build_suggestions(result.files, self.root)
        return suggestions, result

    def suggestion_for(
        self, suggestions: list[organizer.Suggestion], original_rel: str
    ) -> organizer.Suggestion:
        for suggestion in suggestions:
            if suggestion.original_rel == original_rel:
                return suggestion
        self.fail(f"No suggestion for {original_rel}")


class CategorizationTests(unittest.TestCase):
    def test_extension_categories(self) -> None:
        cases = {
            "report.pdf": "Documents",
            "notes.txt": "Documents",
            "book.epub": "Ebooks",
            "table.xlsx": "Spreadsheets",
            "slides.pptx": "Presentations",
            "photo.jpg": "Images",
            "clip.mp4": "Video",
            "voice.mp3": "Audio",
            "backup.zip": "Archives",
            "app.py": "Code",
            "table.csv": "Spreadsheets",
            "font.woff2": "Fonts",
            "setup.exe": "Executables",
            "mystery.xyz": "Other",
        }
        for filename, expected in cases.items():
            with self.subTest(filename=filename):
                category, reason = organizer.categorize_file(filename)
                self.assertEqual(category, expected)
                self.assertIsInstance(reason, str)
                self.assertTrue(reason)

    def test_keyword_categories(self) -> None:
        self.assertEqual(organizer.categorize_file("invoice_acme.pdf")[0], "Finance")
        self.assertEqual(organizer.categorize_file("rent_receipt.pdf")[0], "Finance")


class ScanTests(OrganizerTestCase):
    def test_scan_skips_hidden_and_symlinks(self) -> None:
        self.write("notes.txt")
        self.write(".secret.txt")
        self.write(".hidden/inside.txt")
        self.write("sub/photo.jpg")
        self.write("invoice_acme.pdf")

        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        (self.root / "link.txt").symlink_to(outside / "secret.txt")
        (self.root / "linkdir").symlink_to(outside, target_is_directory=True)

        result = organizer.scan_directory(self.root)
        names = {record.rel_path for record in result.files}

        self.assertEqual(names, {"notes.txt", "sub/photo.jpg", "invoice_acme.pdf"})
        self.assertGreaterEqual(result.skipped_symlinks, 2)
        self.assertFalse(result.truncated)
        self.assertGreaterEqual(result.directories_scanned, 2)

        by_name = {record.rel_path: record for record in result.files}
        self.assertEqual(by_name["invoice_acme.pdf"].category, "Finance")

    def test_scan_truncates_at_max_files(self) -> None:
        for index in range(5):
            self.write(f"file-{index}.txt")
        result = organizer.scan_directory(self.root, max_files=2)
        self.assertEqual(len(result.files), 2)
        self.assertTrue(result.truncated)

    def test_scan_reports_depth_limit(self) -> None:
        self.write("a/b/deep.txt")
        result = organizer.scan_directory(self.root, max_depth=1)
        self.assertEqual([record.rel_path for record in result.files], [])
        self.assertTrue(any("depth" in warning.lower() for warning in result.warnings))


class PreviewTests(OrganizerTestCase):
    def test_preview_never_modifies_filesystem(self) -> None:
        self.write("notes.txt")
        suggestions, _ = self.scan_and_build()
        self.assertEqual(len(suggestions), 1)
        self.assertEqual(suggestions[0].destination_rel, "Documents/notes.txt")
        self.assertTrue(suggestions[0].can_apply)
        self.assertFalse((self.root / "Documents").exists())
        self.assertTrue((self.root / "notes.txt").exists())

    def test_already_organized_file_is_a_noop(self) -> None:
        self.write("Documents/paper.pdf")
        suggestions, _ = self.scan_and_build()
        suggestion = self.suggestion_for(suggestions, "Documents/paper.pdf")
        self.assertEqual(suggestion.action, "none")
        self.assertFalse(suggestion.changed)
        self.assertFalse(suggestion.can_apply)

    def test_existing_destination_is_a_conflict(self) -> None:
        self.write("report.pdf")
        self.write("Documents/report.pdf")
        suggestions, _ = self.scan_and_build()
        suggestion = self.suggestion_for(suggestions, "report.pdf")
        self.assertTrue(suggestion.conflict)
        self.assertIn("already exists", suggestion.conflict_reason)
        self.assertFalse(suggestion.can_apply)

    def test_duplicate_destinations_are_conflicts(self) -> None:
        self.write("one/report.pdf")
        self.write("two/report.pdf")
        suggestions, _ = self.scan_and_build()
        conflicts = [item for item in suggestions if item.conflict]
        self.assertEqual(len(conflicts), 2)
        for suggestion in conflicts:
            self.assertIn("same destination", suggestion.conflict_reason)

    def test_sanitized_names_are_previewed(self) -> None:
        self.write("my  report.pdf")
        suggestions, _ = self.scan_and_build()
        suggestion = self.suggestion_for(suggestions, "my  report.pdf")
        self.assertEqual(suggestion.proposed_name, "my report.pdf")
        self.assertEqual(suggestion.action, "move_rename")
        self.assertFalse(suggestion.conflict)

    def test_symlinked_destination_is_a_conflict(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (self.root / "Code").symlink_to(outside, target_is_directory=True)
        self.write("app.py")

        suggestions, _ = self.scan_and_build()
        suggestion = self.suggestion_for(suggestions, "app.py")
        self.assertTrue(suggestion.conflict)
        self.assertIn("symbolic link", suggestion.conflict_reason)
        self.assertFalse((outside / "app.py").exists())

    def test_ai_overrides_are_validated_and_applied(self) -> None:
        self.write("report.pdf")
        result = organizer.scan_directory(self.root)
        record = result.files[0]
        suggestions = organizer.build_suggestions(
            result.files,
            self.root,
            {
                record.id: {
                    "category": "Finance",
                    "filename": "../evil.pdf",
                    "reason": "Looks like a receipt.",
                }
            },
        )
        suggestion = suggestions[0]
        self.assertEqual(suggestion.category, "Finance")
        self.assertEqual(suggestion.proposed_name, "report.pdf")
        self.assertEqual(suggestion.source, "ai")
        self.assertIn("receipt", suggestion.reason)


class ApplyTests(OrganizerTestCase):
    def make_store(self) -> organizer.OperationStore:
        store = organizer.OperationStore(self.data_dir)
        self.addCleanup(store.close)
        return store

    def test_dry_run_apply_and_undo(self) -> None:
        self.write("notes.txt", "notes")
        self.write("photo.jpg", "image")
        suggestions, _ = self.scan_and_build()
        ids = [item.id for item in suggestions if item.can_apply]
        store = self.make_store()

        dry = organizer.apply_suggestions(
            self.root, suggestions, ids, dry_run=True, store=store
        )
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["summary"]["would_move"], 2)
        self.assertIsNone(dry["operation_id"])
        self.assertTrue((self.root / "notes.txt").exists())

        outcome = organizer.apply_suggestions(
            self.root, suggestions, ids, dry_run=False, store=store
        )
        self.assertEqual(outcome["summary"]["moved"], 2)
        self.assertEqual(outcome["summary"]["failed"], 0)
        self.assertTrue(outcome["operation_id"])
        self.assertFalse((self.root / "notes.txt").exists())
        self.assertEqual(
            (self.root / "Documents" / "notes.txt").read_text(encoding="utf-8"),
            "notes",
        )
        self.assertTrue((self.root / "Images" / "photo.jpg").is_file())

        recent = store.recent(1)[0]
        self.assertEqual(recent["status"], "completed")
        self.assertEqual(len(recent["items"]), 2)

        reopened = organizer.OperationStore(self.data_dir)
        self.addCleanup(reopened.close)
        self.assertEqual(reopened.recent(1)[0]["id"], outcome["operation_id"])

        undone = organizer.undo_operation(reopened, outcome["operation_id"])
        self.assertEqual(undone["status"], "undone")
        self.assertEqual(undone["summary"]["restored"], 2)
        self.assertTrue((self.root / "notes.txt").is_file())
        self.assertTrue((self.root / "photo.jpg").is_file())
        self.assertFalse((self.root / "Documents" / "notes.txt").exists())
        self.assertEqual(reopened.get(outcome["operation_id"])["status"], "undone")

    def test_changed_source_is_skipped_at_apply(self) -> None:
        path = self.write("notes.txt", "one")
        suggestions, _ = self.scan_and_build()
        path.write_text("much longer content", encoding="utf-8")
        store = self.make_store()

        outcome = organizer.apply_suggestions(
            self.root,
            suggestions,
            [suggestions[0].id],
            dry_run=False,
            store=store,
        )
        self.assertEqual(outcome["summary"]["moved"], 0)
        self.assertEqual(outcome["summary"]["skipped"], 1)
        self.assertIn("changed", outcome["results"][0]["message"])
        self.assertTrue(path.exists())
        self.assertFalse((self.root / "Documents").exists())

    def test_destination_appearing_after_preview_is_skipped(self) -> None:
        self.write("notes.txt", "original")
        suggestions, _ = self.scan_and_build()
        destination_dir = self.root / "Documents"
        destination_dir.mkdir()
        destination = destination_dir / "notes.txt"
        destination.write_text("keep", encoding="utf-8")
        store = self.make_store()

        outcome = organizer.apply_suggestions(
            self.root,
            suggestions,
            [suggestions[0].id],
            dry_run=False,
            store=store,
        )
        self.assertEqual(outcome["summary"]["skipped"], 1)
        self.assertTrue((self.root / "notes.txt").exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "keep")

    def test_path_traversal_suggestion_fails_safely(self) -> None:
        outside = self.write("../outside.txt", "outside")
        self.write("report.pdf")
        suggestions, _ = self.scan_and_build()
        bad = dataclasses.replace(suggestions[0], original_rel="../outside.txt")
        store = self.make_store()

        outcome = organizer.apply_suggestions(
            self.root, [bad], [bad.id], dry_run=False, store=store
        )
        self.assertEqual(outcome["summary"]["failed"], 1)
        self.assertEqual(outcome["results"][0]["status"], "failed")
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside")
        self.assertFalse((self.root / "Documents").exists())

    def test_undo_is_blocked_when_original_is_occupied(self) -> None:
        original = self.write("notes.txt", "notes")
        suggestions, _ = self.scan_and_build()
        store = self.make_store()
        outcome = organizer.apply_suggestions(
            self.root,
            suggestions,
            [suggestions[0].id],
            dry_run=False,
            store=store,
        )
        original.write_text("new file", encoding="utf-8")

        undone = organizer.undo_operation(store, outcome["operation_id"])
        self.assertEqual(undone["summary"]["blocked"], 1)
        self.assertEqual(undone["status"], "undo_failed")
        self.assertEqual(original.read_text(encoding="utf-8"), "new file")
        self.assertTrue((self.root / "Documents" / "notes.txt").exists())

    def test_undo_is_blocked_when_moved_file_changed(self) -> None:
        self.write("notes.txt", "notes")
        suggestions, _ = self.scan_and_build()
        store = self.make_store()
        outcome = organizer.apply_suggestions(
            self.root,
            suggestions,
            [suggestions[0].id],
            dry_run=False,
            store=store,
        )
        moved = self.root / "Documents" / "notes.txt"
        moved.write_text("edited after move", encoding="utf-8")

        undone = organizer.undo_operation(store, outcome["operation_id"])
        self.assertEqual(undone["summary"]["blocked"], 1)
        self.assertIn("changed", undone["results"][0]["message"])
        self.assertTrue(moved.exists())
        self.assertFalse((self.root / "notes.txt").exists())

    def test_no_selection_and_unknown_id_do_nothing(self) -> None:
        self.write("notes.txt")
        suggestions, _ = self.scan_and_build()
        store = self.make_store()

        outcome = organizer.apply_suggestions(
            self.root, suggestions, [], dry_run=False, store=store
        )
        self.assertEqual(outcome["summary"]["selected"], 0)
        self.assertIsNone(outcome["operation_id"])

        outcome = organizer.apply_suggestions(
            self.root, suggestions, ["missing"], dry_run=False, store=store
        )
        self.assertEqual(outcome["summary"]["selected"], 0)
        self.assertTrue((self.root / "notes.txt").exists())

    def test_interrupted_operation_is_marked_on_reload(self) -> None:
        store = self.make_store()
        operation_id = store.start(self.root, planned=1)
        store.close()

        reopened = organizer.OperationStore(self.data_dir)
        self.addCleanup(reopened.close)
        operation = reopened.get(operation_id)
        self.assertEqual(operation["status"], "interrupted")
        with self.assertRaises(Exception):
            organizer.undo_operation(reopened, operation_id)


if __name__ == "__main__":
    unittest.main()
