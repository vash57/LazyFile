#!/usr/bin/env python3
"""LazyFile server.

Run with:

    python server.py

The server binds to localhost by default and exposes a small JSON API plus the
static dashboard. No third-party packages are required.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import mimetypes
import platform
import secrets
import sys
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import ai_engine
import config
import organizer
import safety

MAX_REQUEST_BYTES = 1024 * 1024
MAX_SELECTED_IDS = 5000
MAX_SCANS = 6
SCAN_TTL_SECONDS = 2 * 60 * 60


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


@dataclass
class ScanState:
    scan_id: str
    root: Path
    created_at: float
    result: organizer.ScanResult
    suggestions: list[organizer.Suggestion]
    engine: str = "rules"
    model: str = ""
    apply_lock: threading.Lock = field(default_factory=threading.Lock)

    def summary(self) -> dict:
        files = self.result.files
        changed = [item for item in self.suggestions if item.changed]
        pending = [
            item
            for item in changed
            if item.status == "proposed" and not item.conflict
        ]
        conflicts = [
            item
            for item in changed
            if item.conflict and item.status != "applied"
        ]
        applied = [item for item in changed if item.status == "applied"]
        return {
            "files": len(files),
            "categories": len({item.category for item in files}),
            "proposed": len(pending),
            "conflicts": len(conflicts),
            "already_organized": sum(1 for item in self.suggestions if not item.changed),
            "applied": len(applied),
            "directories_scanned": self.result.directories_scanned,
            "skipped_symlinks": self.result.skipped_symlinks,
            "truncated": self.result.truncated,
        }

    def payload(self) -> dict:
        return {
            "scan_id": self.scan_id,
            "root": str(self.root),
            "root_name": self.root.name or str(self.root),
            "engine": self.engine,
            "model": self.model,
            "scanned_at": datetime.fromtimestamp(
                self.created_at, tz=timezone.utc
            ).isoformat(timespec="seconds"),
            "summary": self.summary(),
            "files": [item.to_dict() for item in self.result.files],
            "suggestions": [item.to_dict() for item in self.suggestions],
            "warnings": self.result.warnings,
            "truncated": self.result.truncated,
        }


class LazyFileApp:
    def __init__(
        self,
        settings: config.Settings,
        *,
        ai_client: ai_engine.OllamaClient | None = None,
        store: organizer.OperationStore | None = None,
    ):
        self.settings = settings
        self.ai = ai_client or ai_engine.OllamaClient(
            settings.ollama_url,
            model=settings.ollama_model,
            timeout=settings.ollama_timeout,
            max_files=settings.ai_max_files,
        )
        self.store = store or organizer.OperationStore(settings.data_dir)
        self._scans: OrderedDict[str, ScanState] = OrderedDict()
        self._lock = threading.RLock()
        self._scan_lock = threading.Lock()
        self._ai_lock = threading.Lock()
        self._undo_lock = threading.Lock()
        self.started_at = time.time()

    # ------------------------------------------------------------------ scans
    def _evict_scans(self) -> None:
        now = time.time()
        stale = [
            key
            for key, state in self._scans.items()
            if now - state.created_at > SCAN_TTL_SECONDS
        ]
        for key in stale:
            self._scans.pop(key, None)
        while len(self._scans) > MAX_SCANS:
            self._scans.popitem(last=False)

    def _save_scan(self, state: ScanState) -> None:
        with self._lock:
            self._scans[state.scan_id] = state
            self._scans.move_to_end(state.scan_id)
            self._evict_scans()

    def get_scan(self, scan_id: object) -> ScanState:
        if not isinstance(scan_id, str) or not scan_id:
            raise ApiError(400, "A scan id is required.")
        with self._lock:
            self._evict_scans()
            state = self._scans.get(scan_id)
        if state is None:
            raise ApiError(404, "That scan has expired. Please scan the folder again.")
        return state

    def scan(self, path: object) -> dict:
        root = safety.validate_selected_directory(path)
        with self._scan_lock:
            result = organizer.scan_directory(
                root,
                max_files=self.settings.max_scan_files,
                max_depth=self.settings.max_scan_depth,
            )
            warnings = list(result.warnings)
            if root == Path.home():
                warnings.insert(
                    0,
                    "You selected your entire home folder. A smaller folder such as "
                    "Downloads is usually safer and faster.",
                )
            result.warnings = warnings
            suggestions = organizer.build_suggestions(result.files, root)
            state = ScanState(
                scan_id=secrets.token_urlsafe(18),
                root=root,
                created_at=time.time(),
                result=result,
                suggestions=suggestions,
            )
            self._save_scan(state)
            return state.payload()

    def enhance(self, scan_id: object) -> dict:
        state = self.get_scan(scan_id)
        status = self.ai.status()
        base_payload = {
            "scan_id": state.scan_id,
            "suggestions": [item.to_dict() for item in state.suggestions],
            "summary": state.summary(),
            "engine": state.engine,
            "model": state.model,
            "ai_status": status,
        }
        if not status.get("ready"):
            return {
                **base_payload,
                "ok": False,
                "available": bool(status.get("available")),
                "message": status.get("message", "No local AI model is ready."),
            }

        with self._lock:
            current = state.suggestions
            files_by_id = {item.id: item for item in state.result.files}
            eligible = [
                item
                for item in current
                if item.status == "proposed" and not item.conflict
            ]
            eligible.sort(key=lambda item: (not item.changed, item.original_rel.casefold()))
            eligible = eligible[: self.settings.ai_max_files]

        metadata = []
        for suggestion in eligible:
            record = files_by_id.get(suggestion.file_id)
            if record is None:
                continue
            metadata.append(
                {
                    "id": record.id,
                    "name": record.name,
                    "ext": record.suffix,
                    "size_kb": max(0, record.size // 1024),
                }
            )

        if not metadata:
            return {
                **base_payload,
                "ok": False,
                "available": True,
                "message": "There are no pending files to send to the local model.",
            }

        try:
            with self._ai_lock:
                ai_result = self.ai.suggest(metadata, config.CATEGORIES)
        except (ai_engine.AIError, ai_engine.OllamaUnavailable) as exc:
            return {
                **base_payload,
                "ok": False,
                "available": bool(status.get("available")),
                "message": f"AI suggestions were not applied. {exc}",
            }
        except Exception as exc:
            print(f"[LazyFile] Unexpected AI error: {exc}", file=sys.stderr)
            return {
                **base_payload,
                "ok": False,
                "available": False,
                "message": "AI suggestions were not applied. Rule-based results are intact.",
            }

        with state.apply_lock:
            old_by_file = {item.file_id: item for item in state.suggestions}
            new_suggestions = organizer.build_suggestions(
                state.result.files,
                state.root,
                ai_result.get("overrides", {}),
            )
            for item in new_suggestions:
                previous = old_by_file.get(item.file_id)
                if previous is not None and previous.status == "applied":
                    item.status = "applied"
            state.suggestions = new_suggestions
            state.engine = "ollama"
            state.model = str(ai_result.get("model", ""))

        payload = {
            "ok": True,
            "available": True,
            "scan_id": state.scan_id,
            "suggestions": [item.to_dict() for item in state.suggestions],
            "summary": state.summary(),
            "engine": state.engine,
            "model": state.model,
            "files_sent": ai_result.get("files", len(metadata)),
            "warnings": ai_result.get("warnings", []),
            "message": (
                f"Local AI reviewed {ai_result.get('files', len(metadata))} files "
                f"with {state.model}."
            ),
        }
        return payload

    def apply(
        self, scan_id: object, raw_ids: object, raw_dry_run: object
    ) -> dict:
        state = self.get_scan(scan_id)
        if not isinstance(raw_ids, list) or not all(
            isinstance(item, str) for item in raw_ids
        ):
            raise ApiError(400, "ids must be a list of suggestion ids.")
        if len(raw_ids) > MAX_SELECTED_IDS:
            raise ApiError(400, "Too many suggestions were selected.")
        if not isinstance(raw_dry_run, bool):
            raise ApiError(400, "dry_run must be true or false.")
        ids = list(dict.fromkeys(raw_ids))

        with state.apply_lock:
            outcome = organizer.apply_suggestions(
                state.root,
                state.suggestions,
                ids,
                dry_run=raw_dry_run,
                store=self.store,
            )
            outcome["summary"]["remaining"] = state.summary()["proposed"]
        return outcome

    def operations(self, limit: int = 20) -> dict:
        limit = max(1, min(int(limit), 100))
        return {
            "operations": self.store.recent(limit),
            "durable": self.store.durable,
            "log_error": self.store.error,
        }

    def undo(self, operation_id: object) -> dict:
        if not isinstance(operation_id, str) or not operation_id.strip():
            raise ApiError(400, "An operation id is required.")
        with self._undo_lock:
            try:
                result = organizer.undo_operation(
                    self.store, operation_id.strip()
                )
            except safety.SafetyError as exc:
                raise ApiError(400, str(exc)) from exc
        return result

    def ai_status(self) -> dict:
        try:
            return self.ai.status()
        except Exception as exc:
            return {
                "available": False,
                "reachable": False,
                "ready": False,
                "model": None,
                "configured_model": None,
                "models": [],
                "mode": "rules",
                "message": f"AI status unavailable: {exc}",
            }

    def config_payload(self) -> dict:
        return {
            "version": config.VERSION,
            "categories": [
                {"name": name, **config.CATEGORY_INFO[name]}
                for name in config.CATEGORIES
            ],
            "default_downloads": config.default_downloads_path(),
            "limits": {
                "max_scan_files": self.settings.max_scan_files,
                "max_scan_depth": self.settings.max_scan_depth,
                "ai_max_files": self.settings.ai_max_files,
            },
        }

    def health_payload(self) -> dict:
        return {
            "ok": True,
            "version": config.VERSION,
            "uptime_seconds": int(time.time() - self.started_at),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "operation_log_durable": self.store.durable,
        }

    def close(self) -> None:
        self.store.close()


class LazyFileHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, app: LazyFileApp):
        self.app = app
        super().__init__(address, handler)


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )


class LazyFileHandler(BaseHTTPRequestHandler):
    server_version = f"LazyFile/{config.VERSION}"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    @property
    def app(self) -> LazyFileApp:
        return self.server.app  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        stamp = time.strftime("%H:%M:%S")
        print(f"[{stamp}] {self.address_string()} {fmt % args}", file=sys.stderr)

    # -------------------------------------------------------------- responses
    def _security_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=()",
        )
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _send_error_json(self, status: int, message: str) -> None:
        self.close_connection = True
        self._send_json({"error": message}, status=status)

    def _send_file(self, path: Path) -> None:
        try:
            data = path.read_bytes()
        except OSError:
            self._send_error_json(404, "File not found.")
            return
        content_type, _ = mimetypes.guess_type(str(path))
        if path.suffix == ".js":
            content_type = "text/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif content_type:
            content_type += "; charset=utf-8" if content_type.startswith("text/") else ""

        self.send_response(200)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-cache")
        self._security_headers()
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    # ---------------------------------------------------------------- requests
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path in ("/", "/index.html"):
                self._send_file(config.STATIC_DIR / "index.html")
                return
            if path.startswith("/static/"):
                self._serve_static(path[len("/static/") :])
                return
            if path == "/api/health":
                self._send_json(self.app.health_payload())
                return
            if path == "/api/config":
                self._send_json(self.app.config_payload())
                return
            if path == "/api/ai/status":
                self._send_json(self.app.ai_status())
                return
            if path == "/api/operations":
                query = parse_qs(parsed.query)
                raw_limit = query.get("limit", ["20"])[0]
                try:
                    limit = int(raw_limit)
                except ValueError:
                    limit = 20
                self._send_json(self.app.operations(limit))
                return
            if path.startswith("/api/"):
                raise ApiError(404, "Unknown API route.")
            self._send_error_json(404, "Not found.")
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._send_error_json(500, "An unexpected server error occurred.")

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            self._check_origin()
            self._require_json_content_type()
            body = self._read_json_body()

            if path == "/api/scan":
                payload = self.app.scan(body.get("path"))
            elif path == "/api/suggest":
                payload = self.app.enhance(body.get("scan_id"))
            elif path == "/api/apply":
                payload = self.app.apply(
                    body.get("scan_id"),
                    body.get("ids"),
                    body.get("dry_run", True),
                )
            elif path == "/api/undo":
                payload = self.app.undo(body.get("operation_id"))
            else:
                raise ApiError(404, "Unknown API route.")
            self._send_json(payload)
        except ApiError as exc:
            self._send_error_json(exc.status, exc.message)
        except safety.SafetyError as exc:
            self._send_error_json(400, str(exc))
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            traceback.print_exc()
            self._send_error_json(500, "An unexpected server error occurred.")

    def do_PUT(self) -> None:  # noqa: N802
        self._send_error_json(405, "Method not allowed.")

    def do_DELETE(self) -> None:  # noqa: N802
        self._send_error_json(405, "Method not allowed.")

    def do_PATCH(self) -> None:  # noqa: N802
        self._send_error_json(405, "Method not allowed.")

    def _serve_static(self, relative: str) -> None:
        decoded = unquote(relative)
        if not decoded or "\x00" in decoded or decoded.startswith("/"):
            self._send_error_json(404, "File not found.")
            return
        root = config.STATIC_DIR.resolve()
        candidate = (root / decoded).resolve()
        if candidate != root and root not in candidate.parents:
            self._send_error_json(404, "File not found.")
            return
        if not candidate.is_file():
            self._send_error_json(404, "File not found.")
            return
        self._send_file(candidate)

    def _check_origin(self) -> None:
        origin = self.headers.get("Origin")
        if not origin:
            return
        host = self.headers.get("Host", "")
        if origin not in (f"http://{host}", f"https://{host}"):
            raise ApiError(403, "Cross-origin requests are not allowed.")

    def _require_json_content_type(self) -> None:
        content_type = (
            self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if content_type != "application/json":
            raise ApiError(415, "POST requests must use application/json.")

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise ApiError(411, "Content-Length is required.")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ApiError(400, "Invalid Content-Length.") from exc
        if length <= 0:
            raise ApiError(400, "A JSON request body is required.")
        if length > MAX_REQUEST_BYTES:
            raise ApiError(413, "Request body is too large.")
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ApiError(400, "Request body must be valid JSON.") from exc
        if not isinstance(payload, dict):
            raise ApiError(400, "Request body must be a JSON object.")
        return payload


def create_server(
    host: str, port: int, app: LazyFileApp
) -> LazyFileHTTPServer:
    return LazyFileHTTPServer((host, port), LazyFileHandler, app)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    settings = config.Settings.from_env()
    parser = argparse.ArgumentParser(
        description="LazyFile - a slow and steady local file organizer."
    )
    parser.add_argument(
        "--host",
        default=settings.host,
        help=f"bind address (default: {settings.host})",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=settings.port,
        help=f"port (default: {settings.port})",
    )
    parser.add_argument(
        "--open",
        action="store_true",
        help="open the dashboard in the default browser",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    base_settings = config.Settings.from_env()
    settings = dataclasses.replace(base_settings, host=args.host, port=args.port)
    app = LazyFileApp(settings)

    try:
        server = create_server(settings.host, settings.port, app)
    except OSError as exc:
        print(f"Could not start LazyFile: {exc}", file=sys.stderr)
        app.close()
        return 1

    host, port = server.server_address[:2]
    display_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{display_host}:{port}"
    ai_status = app.ai_status()

    print("")
    print("  LazyFile  |  slow and steady file organization")
    print(f"  Dashboard : {url}")
    print(f"  Data log  : {app.store.path}")
    if ai_status.get("ready"):
        print(f"  AI        : {ai_status.get('model')} (optional)")
    elif ai_status.get("reachable"):
        print(f"  AI        : {ai_status.get('message')}")
    else:
        print("  AI        : Ollama not detected - using the rule engine")
    print("  Preview is the default. Press Ctrl+C to stop.")
    print("")

    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass

    try:
        server.serve_forever(poll_interval=0.4)
    except KeyboardInterrupt:
        print("\nStopping LazyFile...")
    finally:
        server.shutdown()
        server.server_close()
        app.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
