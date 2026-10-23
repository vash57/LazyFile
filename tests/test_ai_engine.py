from __future__ import annotations

import json
import unittest
from unittest import mock

import ai_engine


class ParseTests(unittest.TestCase):
    def test_plain_json(self) -> None:
        self.assertEqual(ai_engine.parse_model_json('{"a": 1}'), {"a": 1})

    def test_fenced_json(self) -> None:
        text = '```json\n{"suggestions": [{"id": "f1"}]}\n```'
        self.assertEqual(
            ai_engine.parse_model_json(text),
            {"suggestions": [{"id": "f1"}]},
        )

    def test_json_embedded_in_prose(self) -> None:
        text = 'Here is the result:\n{"suggestions": []}\nThanks.'
        self.assertEqual(
            ai_engine.parse_model_json(text), {"suggestions": []}
        )

    def test_empty_and_invalid_json(self) -> None:
        for text in ("", "   ", "not json at all", "```json\n```"):
            with self.subTest(text=text):
                with self.assertRaises(ai_engine.AIError):
                    ai_engine.parse_model_json(text)


class ChunkParsingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ai_engine.OllamaClient("http://127.0.0.1:11434")
        self.file_map = {
            "f1": {"id": "f1", "name": "report.pdf", "ext": ".pdf", "size_kb": 4},
            "f2": {"id": "f2", "name": "data.bin", "ext": ".bin", "size_kb": 1},
            "f3": {"id": "f3", "name": "app.py", "ext": ".py", "size_kb": 2},
            "f4": {"id": "f4", "name": "pic.jpg", "ext": ".jpg", "size_kb": 8},
            "f5": {"id": "f5", "name": "notes.txt", "ext": ".txt", "size_kb": 1},
        }
        self.categories = ["Documents", "Finance", "Images", "Code"]

    def test_rejects_unknown_and_invalid_entries(self) -> None:
        text = json.dumps(
            {
                "suggestions": [
                    {
                        "id": "f1",
                        "category": "Finance",
                        "filename": "clean-report.pdf",
                        "reason": "Looks like a receipt.",
                    },
                    {"id": "missing", "category": "Images"},
                    {"id": "f2", "category": "Not a category"},
                    {
                        "id": "f3",
                        "category": "Code",
                        "filename": "../evil.py",
                    },
                    {
                        "id": "f4",
                        "category": "Images",
                        "filename": "photo.png",
                    },
                    {
                        "id": "f5",
                        "category": "Documents",
                        "filename": ".hidden",
                    },
                ]
            }
        )
        overrides = self.client._parse_chunk(text, self.file_map, self.categories)

        self.assertEqual(set(overrides), {"f1", "f3", "f4", "f5"})
        self.assertEqual(overrides["f1"]["category"], "Finance")
        self.assertEqual(overrides["f1"]["filename"], "clean-report.pdf")
        self.assertEqual(overrides["f3"]["category"], "Code")
        self.assertNotIn("filename", overrides["f3"])
        self.assertEqual(overrides["f4"]["category"], "Images")
        self.assertNotIn("filename", overrides["f4"])
        self.assertEqual(overrides["f5"]["category"], "Documents")
        self.assertNotIn("filename", overrides["f5"])

    def test_partial_override_keeps_original_extension(self) -> None:
        text = '{"suggestions": [{"id": "f1", "filename": "cleaner"}]}'
        overrides = self.client._parse_chunk(text, self.file_map, self.categories)
        self.assertEqual(overrides["f1"]["filename"], "cleaner.pdf")

    def test_id_keyed_object_is_accepted(self) -> None:
        text = '{"f1": {"category": "Finance", "reason": "Invoice"}}'
        overrides = self.client._parse_chunk(text, self.file_map, self.categories)
        self.assertEqual(overrides["f1"]["category"], "Finance")

    def test_unsupported_shape_raises(self) -> None:
        with self.assertRaises(ai_engine.AIError):
            self.client._parse_chunk('"just a string"', self.file_map, self.categories)


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ai_engine.OllamaClient("http://127.0.0.1:11434")

    def test_unreachable_ollama_degrades_to_rules(self) -> None:
        with mock.patch.object(
            self.client,
            "_request_json",
            side_effect=ai_engine.OllamaUnavailable("connection refused"),
        ):
            status = self.client.status(force=True)
        self.assertFalse(status["available"])
        self.assertFalse(status["reachable"])
        self.assertFalse(status["ready"])
        self.assertEqual(status["mode"], "rules")
        self.assertIn("offline", status["message"].lower())

    def test_smallest_installed_model_is_selected(self) -> None:
        tags = {
            "models": [
                {"name": "large:13b", "size": 8_000_000_000},
                {"name": "small:1b", "size": 700_000_000},
            ]
        }
        with mock.patch.object(self.client, "_request_json", return_value=tags):
            status = self.client.status(force=True)
        self.assertTrue(status["available"])
        self.assertTrue(status["ready"])
        self.assertEqual(status["model"], "small:1b")
        self.assertEqual(status["mode"], "ai")

    def test_configured_model_is_respected(self) -> None:
        client = ai_engine.OllamaClient(
            "http://127.0.0.1:11434", model="llama3"
        )
        tags = {
            "models": [
                {"name": "tiny:1b", "size": 100},
                {"name": "llama3:8b", "size": 4_000_000_000},
            ]
        }
        with mock.patch.object(client, "_request_json", return_value=tags):
            status = client.status(force=True)
        self.assertTrue(status["ready"])
        self.assertEqual(status["model"], "llama3:8b")
        self.assertEqual(status["configured_model"], "llama3")

    def test_missing_configured_model_disables_ai(self) -> None:
        client = ai_engine.OllamaClient(
            "http://127.0.0.1:11434", model="missing:7b"
        )
        tags = {"models": [{"name": "tiny:1b", "size": 100}]}
        with mock.patch.object(client, "_request_json", return_value=tags):
            status = client.status(force=True)
        self.assertTrue(status["reachable"])
        self.assertFalse(status["ready"])
        self.assertIsNone(status["model"])
        self.assertEqual(status["mode"], "rules")
        self.assertIn("not installed", status["message"])

    def test_no_models_installed(self) -> None:
        with mock.patch.object(
            self.client, "_request_json", return_value={"models": []}
        ):
            status = self.client.status(force=True)
        self.assertTrue(status["reachable"])
        self.assertFalse(status["ready"])
        self.assertIn("no models", status["message"].lower())


class SuggestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = ai_engine.OllamaClient(
            "http://127.0.0.1:11434", max_files=10
        )
        self.ready_status = {
            "available": True,
            "reachable": True,
            "ready": True,
            "model": "tiny:1b",
            "configured_model": None,
            "models": [{"name": "tiny:1b", "size": 1}],
            "mode": "ai",
            "message": "ready",
        }

    def test_suggest_sends_only_minimal_metadata(self) -> None:
        response = {
            "response": json.dumps(
                {
                    "suggestions": [
                        {
                            "id": "f1",
                            "category": "Documents",
                            "filename": "report.pdf",
                        }
                    ]
                }
            )
        }
        files = [
            {"id": "f1", "name": "report.pdf", "ext": ".pdf", "size_kb": 2}
        ]
        with mock.patch.object(
            self.client, "status", return_value=self.ready_status
        ), mock.patch.object(
            self.client, "_request_json", return_value=response
        ) as request:
            result = self.client.suggest(files, ["Documents"])

        self.assertEqual(result["model"], "tiny:1b")
        self.assertEqual(result["files"], 1)
        self.assertIn("f1", result["overrides"])

        args, kwargs = request.call_args
        self.assertEqual(args[0], "/api/generate")
        payload = args[1]
        self.assertEqual(payload["model"], "tiny:1b")
        self.assertFalse(payload["stream"])
        self.assertEqual(payload["format"], "json")
        self.assertIs(payload["think"], False)
        self.assertEqual(payload["keep_alive"], "2m")
        self.assertEqual(payload["options"]["temperature"], 0)
        self.assertEqual(payload["options"]["num_ctx"], 2048)
        prompt = payload["prompt"]
        self.assertIn("report.pdf", prompt)
        self.assertIn("f1", prompt)
        self.assertNotIn("/home/", prompt)
        self.assertNotIn("secret", prompt)
        self.assertEqual(kwargs["timeout"], self.client.timeout)

    def test_suggest_raises_when_no_model_ready(self) -> None:
        status = {
            "available": False,
            "reachable": False,
            "ready": False,
            "model": None,
            "message": "Ollama is offline.",
        }
        with mock.patch.object(self.client, "status", return_value=status):
            with self.assertRaises(ai_engine.OllamaUnavailable):
                self.client.suggest([], ["Documents"])

    def test_suggest_raises_when_single_batch_fails(self) -> None:
        files = [{"id": "f1", "name": "report.pdf", "ext": ".pdf", "size_kb": 1}]
        with mock.patch.object(
            self.client, "status", return_value=self.ready_status
        ), mock.patch.object(
            self.client,
            "_request_json",
            side_effect=ai_engine.AIError("bad response"),
        ):
            with self.assertRaises(ai_engine.AIError):
                self.client.suggest(files, ["Documents"])

    def test_suggest_limits_files(self) -> None:
        client = ai_engine.OllamaClient(
            "http://127.0.0.1:11434", max_files=2
        )
        files = [
            {"id": f"f{index}", "name": f"file{index}.txt", "ext": ".txt", "size_kb": 1}
            for index in range(5)
        ]
        response = {"response": '{"suggestions": []}'}
        with mock.patch.object(
            client, "status", return_value=self.ready_status
        ), mock.patch.object(
            client, "_request_json", return_value=response
        ) as request:
            result = client.suggest(files, ["Documents"])
        self.assertEqual(result["files"], 2)
        prompt = request.call_args.args[1]["prompt"]
        self.assertIn("file0.txt", prompt)
        self.assertIn("file1.txt", prompt)
        self.assertNotIn("file2.txt", prompt)

    def test_invalid_base_url_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ai_engine.OllamaClient("file:///tmp/ollama")


if __name__ == "__main__":
    unittest.main()
