from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import safety


class SafetyTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()

    def write(self, path: Path, content: str = "hello") -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


class FilenameTests(SafetyTestCase):
    def test_sanitize_repairs_paths_and_whitespace(self) -> None:
        self.assertEqual(safety.sanitize_filename("../evil.txt"), "_evil.txt")
        self.assertEqual(
            safety.sanitize_filename("  my   report?.pdf  "),
            "my report_.pdf",
        )
        self.assertEqual(safety.sanitize_filename(r"A/B\C.txt"), "A_B_C.txt")
        self.assertEqual(safety.sanitize_filename("file. "), "file")
        self.assertEqual(safety.sanitize_filename("..."), "file")
        self.assertEqual(safety.sanitize_filename(None, fallback="fallback"), "fallback")

    def test_sanitize_handles_reserved_names(self) -> None:
        self.assertEqual(safety.sanitize_filename("CON.txt"), "_CON.txt")
        self.assertEqual(safety.sanitize_filename("com1.log"), "_com1.log")
        self.assertEqual(safety.sanitize_filename("LPT9"), "_LPT9")
        self.assertEqual(safety.sanitize_filename("COM10.txt"), "COM10.txt")

    def test_sanitize_truncates_utf8_filenames(self) -> None:
        name = safety.sanitize_filename("a" * 300 + ".txt")
        self.assertLessEqual(len(name.encode("utf-8")), safety.MAX_FILENAME_BYTES)
        self.assertTrue(name.endswith(".txt"))

    def test_validate_accepts_regular_filename(self) -> None:
        self.assertEqual(safety.validate_filename("report-2026_final.pdf"), "report-2026_final.pdf")

    def test_validate_rejects_unsafe_filenames(self) -> None:
        bad_names = [
            "",
            ".",
            "..",
            "../escape.txt",
            "a/b.txt",
            r"a\b.txt",
            ".hidden.txt",
            "trailing.",
            "trailing ",
            "control\x01.txt",
            "CON.txt",
            "LPT1.log",
            "nul",
            "x" * 300,
        ]
        for name in bad_names:
            with self.subTest(name=name):
                with self.assertRaises(safety.SafetyError):
                    safety.validate_filename(name)

    def test_hidden_names(self) -> None:
        self.assertTrue(safety.is_hidden_name(".env"))
        self.assertFalse(safety.is_hidden_name("."))
        self.assertFalse(safety.is_hidden_name(".."))
        self.assertFalse(safety.is_hidden_name("visible.txt"))

    def test_unique_names_is_case_insensitive(self) -> None:
        counts = safety.unique_names(["Report.PDF", "report.pdf", "other.txt"])
        self.assertEqual(counts["report.pdf"], 2)
        self.assertEqual(counts["other.txt"], 1)


class DirectoryTests(SafetyTestCase):
    def test_validate_selected_directory(self) -> None:
        selected = safety.validate_selected_directory(str(self.root))
        self.assertEqual(selected.resolve(), self.root.resolve())

    def test_rejects_missing_file_and_root(self) -> None:
        self.write(self.root / "file.txt")
        for candidate in (self.root / "missing", self.root / "file.txt", Path("/")):
            with self.subTest(candidate=candidate):
                with self.assertRaises(safety.SafetyError):
                    safety.validate_selected_directory(str(candidate))
        with self.assertRaises(safety.SafetyError):
            safety.validate_selected_directory("")
        with self.assertRaises(safety.SafetyError):
            safety.validate_selected_directory(None)

    def test_rejects_symlink_selection(self) -> None:
        link = self.base / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(safety.SafetyError):
            safety.validate_selected_directory(str(link))

    def test_safe_join_and_root_containment(self) -> None:
        joined = safety.safe_join(self.root, "Documents", "report.pdf")
        self.assertEqual(joined, self.root / "Documents" / "report.pdf")
        for part in ("", ".", "..", "a/b", r"a\b"):
            with self.subTest(part=part):
                with self.assertRaises(safety.SafetyError):
                    safety.safe_join(self.root, part)
        with self.assertRaises(safety.SafetyError):
            safety.assert_within_root(self.root, self.base / "outside.txt")
        safety.assert_within_root(self.root, self.root, allow_root=True)
        with self.assertRaises(safety.SafetyError):
            safety.assert_within_root(self.root, self.root)

    def test_symlink_components_are_rejected(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret", encoding="utf-8")
        link_dir = self.root / "Documents"
        link_dir.symlink_to(outside, target_is_directory=True)

        self.assertTrue(safety.has_symlink_component(self.root, link_dir / "secret.txt"))
        with self.assertRaises(safety.SafetyError):
            safety.assert_no_symlink_components(self.root, link_dir / "secret.txt")
        with self.assertRaises(safety.SafetyError):
            safety.ensure_safe_directory(self.root, link_dir / "nested")
        self.assertFalse((outside / "nested").exists())

    def test_ensure_safe_directory_creates_nested_folders(self) -> None:
        created = safety.ensure_safe_directory(self.root, self.root / "Documents" / "2026")
        self.assertTrue(created.is_dir())
        self.assertEqual(safety.ensure_safe_directory(self.root, created), created)

    def test_ensure_safe_directory_rejects_file_component(self) -> None:
        self.write(self.root / "Documents")
        with self.assertRaises(safety.SafetyError):
            safety.ensure_safe_directory(self.root, self.root / "Documents" / "nested")


class MoveTests(SafetyTestCase):
    def test_safe_move_links_without_overwrite(self) -> None:
        source = self.write(self.root / "notes.txt", "original")
        destination_dir = self.root / "Documents"
        destination_dir.mkdir()
        destination = destination_dir / "notes.txt"

        method = safety.safe_move(self.root, source, destination)
        self.assertIn(method, {"link", "copy"})
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "original")

    def test_safe_move_never_overwrites(self) -> None:
        source = self.write(self.root / "notes.txt", "source")
        destination_dir = self.root / "Documents"
        destination_dir.mkdir()
        destination = self.write(destination_dir / "notes.txt", "keep")

        with self.assertRaises(FileExistsError):
            safety.safe_move(self.root, source, destination)
        self.assertEqual(source.read_text(encoding="utf-8"), "source")
        self.assertEqual(destination.read_text(encoding="utf-8"), "keep")

    def test_safe_move_copy_fallback(self) -> None:
        source = self.write(self.root / "photo.jpg", "image-bytes")
        destination_dir = self.root / "Images"
        destination_dir.mkdir()
        destination = destination_dir / "photo.jpg"

        with mock.patch.object(
            safety, "_link_no_follow", side_effect=OSError("hard links unavailable")
        ):
            method = safety.safe_move(self.root, source, destination)

        self.assertEqual(method, "copy")
        self.assertFalse(source.exists())
        self.assertEqual(destination.read_text(encoding="utf-8"), "image-bytes")

    def test_safe_move_rejects_paths_outside_root(self) -> None:
        source = self.write(self.root / "notes.txt")
        outside = self.base / "outside"
        outside.mkdir()
        with self.assertRaises(safety.SafetyError):
            safety.safe_move(self.root, source, outside / "notes.txt")
        self.assertTrue(source.exists())

    def test_safe_move_rejects_symlink_source(self) -> None:
        real = self.write(self.base / "real.txt", "secret")
        link = self.root / "link.txt"
        link.symlink_to(real)
        destination_dir = self.root / "Documents"
        destination_dir.mkdir()

        with self.assertRaises(safety.SafetyError):
            safety.safe_move(self.root, link, destination_dir / "link.txt")
        self.assertTrue(real.exists())

    def test_fingerprints(self) -> None:
        path = self.write(self.root / "file.bin", "1234")
        fingerprint = safety.file_fingerprint(path)
        self.assertEqual(fingerprint["size"], 4)
        self.assertTrue(safety.fingerprint_matches(path, fingerprint))
        path.write_text("12345", encoding="utf-8")
        self.assertFalse(safety.fingerprint_matches(path, fingerprint))
        with self.assertRaises(safety.SafetyError):
            safety.file_fingerprint(self.root / "missing.bin")

    def test_fingerprint_rejects_directory(self) -> None:
        with self.assertRaises(safety.SafetyError):
            safety.file_fingerprint(self.root)


if __name__ == "__main__":
    unittest.main()
