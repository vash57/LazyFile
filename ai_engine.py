"""Optional Ollama integration.

Only minimal file metadata is sent to the local daemon. Model output is parsed
as JSON and treated as untrusted: unknown ids, categories, paths, and filename
extensions are rejected before the organizer sees them.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import safety
from config import CATEGORIES

MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_CHUNK_SIZE = 24


class AIError(RuntimeError):
    """Base class for optional AI failures."""


class OllamaUnavailable(AIError):
    """Raised when Ollama cannot be reached or no usable model exists."""


def _clean_text(value: object, limit: int = 240) -> str:
    if not isinstance(value, str):
        return ""
    text = re.sub(r"[\x00-\x1f\x7f]+", " ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


def parse_model_json(text: str) -> object:
    """Extract a JSON value from a model response, including fenced output."""
    if not isinstance(text, str) or not text.strip():
        raise AIError("The local model returned an empty response.")
    candidate = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", candidate, re.DOTALL | re.I)
    if fence:
        candidate = fence.group(1).strip()

    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        pass

    decoder = json.JSONDecoder()
    for index, char in enumerate(candidate):
        if char not in "[{":
            continue
        try:
            value, _ = decoder.raw_decode(candidate[index:])
            return value
        except (json.JSONDecodeError, ValueError):
            continue
    raise AIError("The local model did not return valid JSON.")


def _model_matches(configured: str, installed: str) -> bool:
    left = configured.casefold()
    right = installed.casefold()
    return left == right or right.startswith(left + ":") or left.startswith(right + ":")


class OllamaClient:
    def __init__(
        self,
        base_url: str,
        model: str = "",
        timeout: float = 60.0,
        max_files: int = 80,
    ):
        parsed = urlparse(base_url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise ValueError("OLLAMA_URL must be an http(s) URL.")
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout = max(3.0, float(timeout))
        self.max_files = max(1, int(max_files))
        self._lock = threading.Lock()
        self._cached_status: dict | None = None
        self._cached_at = 0.0
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({})
        )

    def _request_json(
        self,
        endpoint: str,
        payload: dict | None = None,
        *,
        timeout: float | None = None,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> dict:
        url = self.base_url + endpoint
        data = None
        headers = {
            "Accept": "application/json",
            "User-Agent": "LazyFile/1.0",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url, data=data, headers=headers, method="POST" if data else "GET"
        )
        try:
            with self._opener.open(
                request, timeout=timeout or self.timeout
            ) as response:
                raw = response.read(max_bytes + 1)
        except urllib.error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read(500).decode("utf-8", "replace")
            except Exception:
                pass
            raise AIError(
                f"Ollama returned HTTP {exc.code}"
                + (f": {_clean_text(detail, 200)}" if detail else ".")
            ) from exc
        except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
            reason = getattr(exc, "reason", exc)
            raise OllamaUnavailable(
                f"Ollama is not reachable at {self.base_url} ({reason})."
            ) from exc
        except OSError as exc:
            raise OllamaUnavailable(
                f"Ollama is not reachable at {self.base_url} ({exc})."
            ) from exc

        if len(raw) > max_bytes:
            raise AIError("Ollama returned an unexpectedly large response.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            raise AIError("Ollama returned a malformed JSON response.") from exc
        if not isinstance(decoded, dict):
            raise AIError("Ollama returned an unexpected response shape.")
        return decoded

    def status(self, *, force: bool = False) -> dict:
        with self._lock:
            now = time.monotonic()
            if (
                not force
                and self._cached_status is not None
                and now - self._cached_at < 10.0
            ):
                return dict(self._cached_status)

        try:
            data = self._request_json(
                "/api/tags", timeout=min(2.0, self.timeout), max_bytes=1024 * 1024
            )
        except (AIError, OllamaUnavailable) as exc:
            result = {
                "available": False,
                "reachable": False,
                "ready": False,
                "model": self.model or None,
                "configured_model": self.model or None,
                "models": [],
                "mode": "rules",
                "message": f"Ollama is offline. {exc}",
            }
            with self._lock:
                self._cached_status = result
                self._cached_at = time.monotonic()
            return dict(result)

        models = []
        raw_models = data.get("models")
        if isinstance(raw_models, list):
            for item in raw_models:
                if not isinstance(item, dict):
                    continue
                name = item.get("name")
                if not isinstance(name, str) or not name.strip():
                    continue
                try:
                    size = int(item.get("size", 0) or 0)
                except (TypeError, ValueError):
                    size = 0
                models.append({"name": name.strip(), "size": size})
        models.sort(key=lambda item: (item["size"] or float("inf"), item["name"]))

        selected: str | None = None
        ready = False
        if self.model:
            match = next(
                (
                    item["name"]
                    for item in models
                    if _model_matches(self.model, item["name"])
                ),
                None,
            )
            if match:
                selected = match
                ready = True
        elif models:
            selected = models[0]["name"]
            ready = True

        if not models:
            message = (
                "Ollama is running, but no models are installed. "
                "Rule-based suggestions remain active."
            )
        elif self.model and not ready:
            message = (
                f"Configured model '{self.model}' is not installed. "
                "Rule-based suggestions remain active."
            )
        elif ready:
            message = (
                f"Local AI ready with {selected}. "
                "Only filenames and sizes are sent."
            )
        else:
            message = "Ollama is running, but no usable model was found."

        result = {
            "available": True,
            "reachable": True,
            "ready": ready,
            "model": selected,
            "configured_model": self.model or None,
            "models": models,
            "mode": "ai" if ready else "rules",
            "message": message,
        }
        with self._lock:
            self._cached_status = result
            self._cached_at = time.monotonic()
        return dict(result)

    def _build_prompt(self, files: list[dict], categories: list[str]) -> str:
        metadata = json.dumps(files, ensure_ascii=False, separators=(",", ":"))
        return (
            "You are a careful file organization assistant.\n"
            "Choose one category and a clean filename for every file.\n"
            f"Allowed categories: {', '.join(categories)}.\n"
            "Rules:\n"
            "- Keep the original file extension exactly.\n"
            "- Filenames must not contain paths, slashes, or control characters.\n"
            "- Do not invent categories or files.\n"
            "- Reasons must be short (under 160 characters).\n"
            "Return JSON only in this exact shape:\n"
            '{"suggestions":[{"id":"f_123","category":"Documents",'
            '"filename":"invoice-2026.pdf","reason":"A finance document."}]}\n'
            f"Files: {metadata}"
        )

    def _parse_chunk(
        self, response_text: str, file_map: dict[str, dict], categories: list[str]
    ) -> dict[str, dict]:
        parsed = parse_model_json(response_text)
        raw_items: list[object]

        if isinstance(parsed, list):
            raw_items = parsed
        elif isinstance(parsed, dict):
            candidate = parsed.get("suggestions")
            if isinstance(candidate, list):
                raw_items = candidate
            else:
                raw_items = []
                for key, value in parsed.items():
                    if key in file_map and isinstance(value, dict):
                        entry = dict(value)
                        entry.setdefault("id", key)
                        raw_items.append(entry)
        else:
            raise AIError("The local model returned an unsupported JSON shape.")

        category_lookup = {item.casefold(): item for item in categories}
        overrides: dict[str, dict] = {}

        for item in raw_items:
            if not isinstance(item, dict):
                continue
            raw_id = item.get("id")
            if not isinstance(raw_id, str):
                continue
            file_id = raw_id.strip()
            if file_id not in file_map:
                continue

            original_name = str(file_map[file_id].get("name", ""))
            original_suffix = Path(original_name).suffix

            raw_category = item.get("category")
            category = None
            if isinstance(raw_category, str):
                category = category_lookup.get(raw_category.strip().casefold())

            filename: str | None = None
            raw_filename = item.get("filename")
            if isinstance(raw_filename, str) and raw_filename.strip():
                candidate = raw_filename.strip()
                if (
                    "/" not in candidate
                    and "\\" not in candidate
                    and not candidate.startswith(".")
                ):
                    candidate = safety.sanitize_filename(candidate, fallback="file")
                    if not Path(candidate).suffix and original_suffix:
                        candidate += original_suffix
                    candidate_suffix = Path(candidate).suffix
                    if (
                        not original_suffix
                        or candidate_suffix.casefold()
                        == original_suffix.casefold()
                    ):
                        try:
                            safety.validate_filename(candidate)
                        except safety.SafetyError:
                            filename = None
                        else:
                            filename = candidate

            if not category and not filename:
                continue

            override: dict[str, str] = {}
            if category:
                override["category"] = category
            if filename:
                override["filename"] = filename
            reason = _clean_text(item.get("reason"), 240)
            override["reason"] = reason or (
                f"Local AI organized this as {category or 'a cleaner file'}."
            )
            overrides[file_id] = override

        return overrides

    def suggest(self, files: list[dict], categories: list[str] | None = None) -> dict:
        """Ask the configured local model for validated overrides."""
        categories = list(categories or CATEGORIES)
        status = self.status()
        if not status.get("ready") or not status.get("model"):
            raise OllamaUnavailable(str(status.get("message", "No model is ready.")))

        cleaned_files: list[dict] = []
        file_map: dict[str, dict] = {}
        for item in files:
            if not isinstance(item, dict):
                continue
            file_id = item.get("id")
            name = item.get("name")
            if not isinstance(file_id, str) or not isinstance(name, str):
                continue
            if not file_id or len(file_id) > 80 or len(name) > 512:
                continue
            entry = {
                "id": file_id,
                "name": name,
                "ext": str(item.get("ext", ""))[:16],
                "size_kb": max(0, int(item.get("size_kb", 0) or 0)),
            }
            cleaned_files.append(entry)
            file_map[file_id] = entry
            if len(cleaned_files) >= self.max_files:
                break

        if not cleaned_files:
            return {
                "overrides": {},
                "model": status["model"],
                "files": 0,
                "chunks": 0,
                "warnings": [],
            }

        overrides: dict[str, dict] = {}
        warnings: list[str] = []
        chunks = 0
        failures = 0

        for start in range(0, len(cleaned_files), _CHUNK_SIZE):
            chunk = cleaned_files[start : start + _CHUNK_SIZE]
            chunks += 1
            prompt = self._build_prompt(chunk, categories)
            num_predict = min(1400, max(300, len(chunk) * 90))
            try:
                response = self._request_json(
                    "/api/generate",
                    {
                        "model": status["model"],
                        "prompt": prompt,
                        "stream": False,
                        "format": "json",
                        "think": False,
                        "keep_alive": "2m",
                        "options": {
                            "temperature": 0,
                            "num_predict": num_predict,
                            "num_ctx": 2048,
                        },
                    },
                    timeout=self.timeout,
                )
                text = response.get("response")
                if not isinstance(text, str):
                    raise AIError("Ollama did not include a model response.")
                overrides.update(self._parse_chunk(text, file_map, categories))
            except (AIError, OllamaUnavailable) as exc:
                failures += 1
                warnings.append(_clean_text(str(exc), 240))
                if failures == chunks and chunks == 1:
                    raise

        if chunks and failures == chunks:
            raise AIError(warnings[-1] if warnings else "The local model failed.")
        if failures:
            warnings.append(
                f"{failures} of {chunks} AI batches failed; rule-based results were kept "
                "for those files."
            )

        return {
            "overrides": overrides,
            "model": status["model"],
            "files": len(cleaned_files),
            "chunks": chunks,
            "warnings": warnings,
        }
