# LazyFile

**A slow and steady, local-first file organizer.** LazyFile scans a folder, previews every proposed move, and only changes files after you explicitly confirm. It works completely offline with a deterministic rule engine, and can optionally use a local Ollama model for smarter names and categories.

Built for **DEV Hacktoberfest Week 3 2026 — Slow and Steady**. No pip packages, no cloud service, no telemetry, and no deletes.

```text
python3 server.py
# open http://127.0.0.1:8765
```

## Why LazyFile is deliberately cautious

Most organizers ask you to trust a black box with your files. LazyFile makes the safe path the default:

- **Preview first.** Scanning never creates folders or moves files.
- **Explicit selection.** You choose which suggestions to apply.
- **Dry runs by default.** The API defaults to `dry_run=true`; the dashboard also offers a dry-run toggle.
- **No overwrites.** Moves use a hard link plus unlink when possible, with an exclusive-create copy fallback. A conflicting destination is always skipped.
- **No deletes.** The only removal is the source entry after its new destination is safely in place.
- **Rechecks at apply time.** File size/mtime fingerprints and destination collisions are verified again after preview.
- **Path containment.** Paths, filenames, hidden entries, and symbolic links are validated. Symlinks are never followed.
- **Undo with verification.** Undo only restores a file when its destination still matches the fingerprint recorded at move time.
- **Local-only AI.** If you opt in, Ollama receives only `id`, filename, extension, and size in KB.

## Features

- Rule-based categorization into 14 useful categories.
- Clean filename proposals, including collision detection.
- Recursive scan with configurable file and depth limits.
- Search, category filters, per-file selection, select-all, and load-more.
- Visual preview showing source, destination, category, reason, and conflicts.
- Apply selected suggestions with a dry-run option.
- Persistent JSONL operation log with one-click undo.
- Activity view with moved/restored paths.
- Optional Ollama integration with model detection, strict JSON validation, and automatic rule fallback.
- Responsive dark dashboard with no build step and no external assets.
- Standard-library-only backend: `http.server`, `threading`, `unittest`.
- CSP, `nosniff`, frame denial, same-origin checks, and JSON content-type enforcement.

## Requirements

- Python **3.10+** (tested on Python 3.14.6).
- Any modern browser.
- Optional: [Ollama](https://ollama.com/) for local AI suggestions.

No virtual environment or package installation is required.

## Quick start

```bash
cd LazyFile
python3 server.py
```

Open `http://127.0.0.1:8765`.

The server binds to localhost by default. Use `--host` and `--port` to change that:

```bash
python3 server.py --host 127.0.0.1 --port 9000
```

Use `--open` to launch the dashboard in your default browser.

## Optional Ollama setup

LazyFile is fully functional without AI. To enable local model suggestions:

```bash
ollama serve
ollama pull llama3.2:1b
python3 server.py
```

If exactly one model is installed, LazyFile selects it. With multiple models and no configuration, it chooses the smallest installed model. To pin a model:

```bash
OLLAMA_MODEL=llama3.2:1b python3 server.py
```

If Ollama is offline, no model is installed, or the configured model is missing, LazyFile keeps the rule-based preview and reports the reason in the UI. Nothing is downloaded automatically.

Only this metadata is sent to the model:

```json
{"id": "f_abc123", "name": "invoice-acme.pdf", "ext": ".pdf", "size_kb": 42}
```

Model responses are validated: unknown file IDs, unknown categories, path separators, hidden filenames, and changed extensions are rejected or sanitized.

Thinking models are asked to skip their reasoning stream so the JSON answer is returned directly.

### AI troubleshooting

If Ollama runs on Windows and LazyFile runs inside WSL2, `127.0.0.1` points at WSL, not Windows. Either run LazyFile on Windows, or use WSL mirrored networking and point `OLLAMA_URL` at `http://127.0.0.1:11434`. With the default NAT networking, use the Windows host gateway instead:

```bash
OLLAMA_URL=http://$(ip route show default | awk '{print $3}'):11434 python3 server.py
```

Windows Firewall must allow inbound TCP 11434. On Windows 11 with Hyper-V firewall, an elevated PowerShell rule may also be required:

```powershell
New-NetFirewallHyperVRule -Name "Ollama-WSL" -DisplayName "Ollama from WSL" `
  -Direction Inbound -VMCreatorId "{40E0AC32-46A5-438A-A0B2-2B479E8F2E90}" `
  -Protocol TCP -LocalPorts 11434
```

Tiny models (`llama3.2:1b`, `qwen2.5:0.5b`) are strongly preferred on low-power machines. Large or thinking-capable models can take a minute or more to load and may exceed `OLLAMA_TIMEOUT`; LazyFile then keeps the rule-based preview.


## How to use it

1. Enter a folder or click **Downloads**.
2. Click **Scan files**. This only reads file metadata.
3. Review the proposals. Conflicts are marked and cannot be applied.
4. Select the files you want, or use **Select all safe**.
5. Leave **Dry run** enabled for a simulation, or turn it off for a real move.
6. Click **Simulate moves** or **Apply moves**, then confirm.
7. If needed, click **Undo** in the result or in the **Activity** view.

Destination folders are created as `<selected folder>/<Category>/`, for example `Downloads/Documents/report.pdf`.

## Categories

`Documents`, `Finance`, `Spreadsheets`, `Presentations`, `Images`, `Video`, `Audio`, `Archives`, `Code`, `Data`, `Ebooks`, `Executables`, `Fonts`, and `Other`.

Rules consider both extension and useful filename keywords. For example, `invoice-acme.pdf` becomes Finance while `report.pdf` becomes Documents, and `screenshot.png` becomes Images.

## Configuration

Copy `.env.example` to `.env`, or export variables in your shell.

| Variable | Default | Purpose |
| --- | --- | --- |
| `LAZYFILE_HOST` | `127.0.0.1` | Bind address |
| `LAZYFILE_PORT` | `8765` | HTTP port |
| `LAZYFILE_DATA_DIR` | `~/.lazyfile` | Operation log location |
| `LAZYFILE_MAX_FILES` | `3000` | Maximum files per scan |
| `LAZYFILE_MAX_DEPTH` | `16` | Maximum recursive depth |
| `LAZYFILE_AI_MAX_FILES` | `80` | Maximum files sent to Ollama |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Ollama endpoint |
| `OLLAMA_MODEL` | empty | Pin a model; empty selects the smallest |
| `OLLAMA_TIMEOUT` | `60` | Request timeout in seconds |

The operation journal is append-only JSONL at `~/.lazyfile/operations.jsonl`. Set `LAZYFILE_DATA_DIR` to put it somewhere else.

## API

The dashboard uses the same JSON API that you can call directly.

| Method | Route | Purpose |
| --- | --- | --- |
| `GET` | `/api/health` | Version, uptime, platform, log durability |
| `GET` | `/api/config` | Categories, defaults, limits |
| `GET` | `/api/ai/status` | Ollama availability and selected model |
| `GET` | `/api/operations?limit=20` | Recent operations and undoability |
| `POST` | `/api/scan` | Scan `{"path": "/folder"}` |
| `POST` | `/api/suggest` | Apply local AI to `{"scan_id": "..."}` |
| `POST` | `/api/apply` | Apply `{"scan_id": "...", "ids": [...], "dry_run": true}` |
| `POST` | `/api/undo` | Undo `{"operation_id": "..."}` |

POST requests must use `Content-Type: application/json`. If an `Origin` header is present, it must match the server host. Scan IDs are kept in memory and expire after two hours or when older scans are evicted.

## Project structure

```text
LazyFile/
├── server.py           # HTTP server, routes, CLI
├── organizer.py        # scan, rules, previews, apply/undo, JSONL journal
├── safety.py           # filename/path validation and no-overwrite moves
├── ai_engine.py        # optional Ollama client and response validation
├── config.py           # settings, categories, extension/keyword rules
├── static/
│   ├── index.html      # dashboard markup
│   ├── style.css       # dark responsive design
│   ├── app.js          # client state and rendering
│   └── favicon.svg
├── tests/              # standard library unittest suite
├── .env.example
├── LICENSE
└── README.md
```

## Tests

```bash
python3 -m unittest discover -s tests -t . -v
```

The suite covers filename sanitization, path traversal and symlinks, no-overwrite moves, the copy fallback, categorization, preview conflicts, dry runs, real applies, expired scans, undo blocking, JSONL persistence, Ollama parsing/status/fallback, HTTP routes, and request security.

## Design notes and limits

- Files are organized in place under the selected folder. Cross-filesystem moves are handled by the copy fallback.
- Hidden files and folders are intentionally skipped, as are symbolic links.
- The operation log stores absolute source/destination paths and fingerprints so undo can verify state.
- A file changed after preview is skipped rather than overwritten.
- Scan state is in memory. Restarting the server clears scans but keeps the operation log, so earlier operations can still be undone.
- The AI is an optional enhancer, not a dependency. Rule-based suggestions remain the baseline.
- LazyFile is a local tool intended for localhost use. Do not expose it to the public internet without adding authentication and TLS.

## License

MIT © 2026 LazyFile contributors. See [LICENSE](LICENSE).
