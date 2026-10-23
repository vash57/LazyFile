from __future__ import annotations

import dataclasses
import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

import ai_engine
import config
import server


class OfflineAI:
    def status(self, *, force: bool = False) -> dict:
        return {
            "available": False,
            "reachable": False,
            "ready": False,
            "model": None,
            "configured_model": None,
            "models": [],
            "mode": "rules",
            "message": "Ollama is offline.",
        }

    def suggest(self, files: list[dict], categories: list[str] | None = None) -> dict:
        raise ai_engine.OllamaUnavailable("Ollama is offline.")


class ReadyAI:
    def __init__(self, file_id: str):
        self.file_id = file_id

    def status(self, *, force: bool = False) -> dict:
        return {
            "available": True,
            "reachable": True,
            "ready": True,
            "model": "fake:1b",
            "configured_model": None,
            "models": [{"name": "fake:1b", "size": 1}],
            "mode": "ai",
            "message": "Local AI ready.",
        }

    def suggest(self, files: list[dict], categories: list[str] | None = None) -> dict:
        return {
            "overrides": {
                self.file_id: {
                    "category": "Finance",
                    "filename": "acme-invoice.pdf",
                    "reason": "Looks like an invoice.",
                }
            },
            "model": "fake:1b",
            "files": len(files),
            "chunks": 1,
            "warnings": [],
        }


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / "root"
        self.root.mkdir()

        self.settings = dataclasses.replace(
            config.Settings.from_env(),
            host="127.0.0.1",
            port=0,
            data_dir=self.base / "data",
            max_scan_files=100,
        )
        self.app = server.LazyFileApp(self.settings, ai_client=OfflineAI())
        self.httpd = server.create_server("127.0.0.1", 0, self.app)
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()
        self.host, self.port = self.httpd.server_address[:2]
        self.base_url = f"http://{self.host}:{self.port}"

        self.log_patch = mock.patch.object(
            server.LazyFileHandler, "log_message", lambda *args, **kwargs: None
        )
        self.log_patch.start()
        self.addCleanup(self._stop)

    def _stop(self) -> None:
        try:
            self.httpd.shutdown()
        finally:
            self.httpd.server_close()
            self.thread.join(timeout=5)
            self.app.close()
            self.log_patch.stop()
            self.temp.cleanup()

    def write(self, relative: str, content: str = "data") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def request(
        self,
        path: str,
        method: str = "GET",
        payload: dict | None = None,
        *,
        raw: bytes | None = None,
        headers: dict[str, str] | None = None,
        content_type: str | None = "application/json",
    ) -> tuple[int, object]:
        data = raw
        if data is None and payload is not None:
            data = json.dumps(payload).encode("utf-8")
        request_headers = {"Accept": "application/json"}
        if headers:
            request_headers.update(headers)
        if data is not None and content_type is not None:
            request_headers.setdefault("Content-Type", content_type)
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=request_headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read()
                response_type = response.headers.get("Content-Type", "")
                if "json" in response_type:
                    return response.status, json.loads(body.decode("utf-8"))
                return response.status, body.decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read()
                response_type = exc.headers.get("Content-Type", "")
                if "json" in response_type:
                    return exc.code, json.loads(body.decode("utf-8"))
                return exc.code, body.decode("utf-8", "replace")
            finally:
                exc.close()

    def scan(self) -> tuple[str, dict]:
        status, payload = self.request(
            "/api/scan", method="POST", payload={"path": str(self.root)}
        )
        self.assertEqual(status, 200, payload)
        self.assertIsInstance(payload, dict)
        return payload["scan_id"], payload


class PageTests(ServerTestCase):
    def test_dashboard_and_static_assets(self) -> None:
        status, body = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("LazyFile", body)
        self.assertIn("app.js", body)

        status, body = self.request("/static/app.js")
        self.assertEqual(status, 200)
        self.assertIn("const state", body)

        status, body = self.request("/static/style.css")
        self.assertEqual(status, 200)
        self.assertIn("--accent", body)

    def test_health_config_and_ai_status(self) -> None:
        status, health = self.request("/api/health")
        self.assertEqual(status, 200)
        self.assertTrue(health["ok"])
        self.assertEqual(health["version"], config.VERSION)

        status, payload = self.request("/api/config")
        self.assertEqual(status, 200)
        self.assertIn("categories", payload)
        self.assertIn("limits", payload)
        self.assertEqual(payload["version"], config.VERSION)

        status, ai = self.request("/api/ai/status")
        self.assertEqual(status, 200)
        self.assertFalse(ai["available"])
        self.assertEqual(ai["mode"], "rules")

    def test_static_traversal_is_blocked(self) -> None:
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            connection.request("GET", "/static/%2e%2e/server.py")
            response = connection.getresponse()
            response.read()
            self.assertEqual(response.status, 404)
        finally:
            connection.close()

    def test_unknown_routes(self) -> None:
        status, _ = self.request("/api/does-not-exist")
        self.assertEqual(status, 404)
        status, _ = self.request("/not-a-page")
        self.assertEqual(status, 404)


class ScanApiTests(ServerTestCase):
    def test_scan_returns_preview_without_moving_files(self) -> None:
        self.write("notes.txt", "notes")
        self.write("photo.jpg", "photo")
        self.write(".hidden.txt", "hidden")

        scan_id, payload = self.scan()
        self.assertTrue(scan_id)
        self.assertEqual(payload["summary"]["files"], 2)
        self.assertEqual(len(payload["suggestions"]), 2)
        self.assertTrue(all(item["status"] == "proposed" for item in payload["suggestions"]))
        self.assertFalse((self.root / "Documents").exists())
        self.assertFalse((self.root / "Images").exists())
        self.assertTrue((self.root / "notes.txt").exists())

    def test_scan_rejects_invalid_paths(self) -> None:
        link = self.base / "link"
        link.symlink_to(self.root, target_is_directory=True)
        cases = [
            {},
            {"path": ""},
            {"path": "/"},
            {"path": str(self.base / "missing")},
            {"path": str(link)},
        ]
        for payload in cases:
            with self.subTest(payload=payload):
                status, body = self.request(
                    "/api/scan", method="POST", payload=payload
                )
                self.assertEqual(status, 400)
                self.assertIsInstance(body["error"], str)

    def test_ai_fallback_keeps_rule_suggestions(self) -> None:
        self.write("notes.txt")
        scan_id, payload = self.scan()

        status, enhanced = self.request(
            "/api/suggest", method="POST", payload={"scan_id": scan_id}
        )
        self.assertEqual(status, 200)
        self.assertFalse(enhanced["ok"])
        self.assertFalse(enhanced["available"])
        self.assertIn("offline", enhanced["message"].lower())
        self.assertEqual(
            [item["id"] for item in enhanced["suggestions"]],
            [item["id"] for item in payload["suggestions"]],
        )

    def test_ai_suggestions_are_integrated_and_validated(self) -> None:
        self.write("report.pdf")
        scan_id, payload = self.scan()
        file_id = payload["suggestions"][0]["file_id"]
        self.app.ai = ReadyAI(file_id)

        status, enhanced = self.request(
            "/api/suggest", method="POST", payload={"scan_id": scan_id}
        )
        self.assertEqual(status, 200)
        self.assertTrue(enhanced["ok"])
        self.assertEqual(enhanced["engine"], "ollama")
        self.assertEqual(enhanced["model"], "fake:1b")
        suggestion = enhanced["suggestions"][0]
        self.assertEqual(suggestion["category"], "Finance")
        self.assertEqual(suggestion["proposed_name"], "acme-invoice.pdf")
        self.assertEqual(suggestion["source"], "ai")
        self.assertTrue(suggestion["can_apply"])

    def test_suggest_rejects_expired_scan(self) -> None:
        status, _ = self.request(
            "/api/suggest", method="POST", payload={"scan_id": "missing"}
        )
        self.assertEqual(status, 404)


class ApplyApiTests(ServerTestCase):
    def test_apply_operations_and_undo(self) -> None:
        self.write("notes.txt", "notes")
        self.write("photo.jpg", "photo")
        scan_id, payload = self.scan()
        ids = [item["id"] for item in payload["suggestions"] if item["can_apply"]]
        self.assertEqual(len(ids), 2)

        status, dry = self.request(
            "/api/apply", method="POST", payload={"scan_id": scan_id, "ids": ids}
        )
        self.assertEqual(status, 200)
        self.assertTrue(dry["dry_run"])
        self.assertEqual(dry["summary"]["would_move"], 2)
        self.assertIsNone(dry["operation_id"])
        self.assertFalse((self.root / "Documents").exists())

        status, applied = self.request(
            "/api/apply",
            method="POST",
            payload={"scan_id": scan_id, "ids": ids, "dry_run": False},
        )
        self.assertEqual(status, 200)
        self.assertEqual(applied["summary"]["moved"], 2)
        self.assertEqual(applied["summary"]["failed"], 0)
        operation_id = applied["operation_id"]
        self.assertTrue(operation_id)
        self.assertTrue((self.root / "Documents" / "notes.txt").is_file())
        self.assertTrue((self.root / "Images" / "photo.jpg").is_file())

        status, operations = self.request("/api/operations?limit=5")
        self.assertEqual(status, 200)
        self.assertTrue(operations["durable"])
        self.assertEqual(operations["operations"][0]["id"], operation_id)
        self.assertEqual(operations["operations"][0]["status"], "completed")
        self.assertTrue(operations["operations"][0]["undoable"])

        status, undone = self.request(
            "/api/undo", method="POST", payload={"operation_id": operation_id}
        )
        self.assertEqual(status, 200)
        self.assertEqual(undone["status"], "undone")
        self.assertEqual(undone["summary"]["restored"], 2)
        self.assertTrue((self.root / "notes.txt").is_file())
        self.assertTrue((self.root / "photo.jpg").is_file())

        status, operations = self.request("/api/operations?limit=5")
        self.assertEqual(operations["operations"][0]["status"], "undone")

    def test_apply_validates_selection_and_dry_run(self) -> None:
        self.write("notes.txt")
        scan_id, _ = self.scan()

        invalid_payloads = [
            {"scan_id": scan_id, "ids": "not-a-list", "dry_run": False},
            {"scan_id": scan_id, "ids": [1, 2], "dry_run": False},
            {"scan_id": scan_id, "ids": [], "dry_run": "yes"},
        ]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                status, body = self.request(
                    "/api/apply", method="POST", payload=payload
                )
                self.assertEqual(status, 400)
                self.assertIsInstance(body["error"], str)

    def test_undo_validation(self) -> None:
        for payload in ({}, {"operation_id": ""}, {"operation_id": 42}):
            with self.subTest(payload=payload):
                status, body = self.request(
                    "/api/undo", method="POST", payload=payload
                )
                self.assertEqual(status, 400)
        status, _ = self.request(
            "/api/undo", method="POST", payload={"operation_id": "op_missing"}
        )
        self.assertEqual(status, 400)


class RequestSecurityTests(ServerTestCase):
    def test_post_requires_json_and_same_origin(self) -> None:
        status, _ = self.request(
            "/api/scan",
            method="POST",
            raw=b'{"path": "/tmp"}',
            content_type="text/plain",
        )
        self.assertEqual(status, 415)

        status, _ = self.request(
            "/api/scan",
            method="POST",
            payload={"path": str(self.root)},
            headers={"Origin": "http://evil.example"},
        )
        self.assertEqual(status, 403)

        status, _ = self.request(
            "/api/scan",
            method="POST",
            payload={"path": str(self.root)},
            headers={"Origin": self.base_url},
        )
        self.assertEqual(status, 200)

    def test_malformed_json_is_rejected(self) -> None:
        status, body = self.request("/api/scan", method="POST", raw=b"not json")
        self.assertEqual(status, 400)
        self.assertIsInstance(body["error"], str)

    def test_unknown_post_route(self) -> None:
        status, body = self.request("/api/nope", method="POST", payload={})
        self.assertEqual(status, 404)
        self.assertIsInstance(body["error"], str)

    def test_unsupported_methods(self) -> None:
        for method in ("PUT", "DELETE", "PATCH"):
            with self.subTest(method=method):
                status, _ = self.request("/api/config", method=method)
                self.assertEqual(status, 405)


if __name__ == "__main__":
    unittest.main()
