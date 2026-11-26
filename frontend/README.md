# NijSetu dashboard

React + TypeScript + Vite dashboard connected to the existing Python ingest, detection, identity resolution, sanitization and validation engine through `nijsetu.web`. Both the demo dataset and judges’ uploads run the real engine.

## Start the complete app

Requires Python 3.10+, Node.js 22.12+ and npm. From the repository root:

```bash
bash scripts/start-dashboard.sh
```

This installs missing frontend dependencies, builds the frontend and starts the Python API serving the built dashboard at **http://127.0.0.1:8765**. Dependencies need internet only during installation. The running app works offline. Stop with Ctrl+C. You can pass `--port 8766` to use another port.

For development, use two terminals from the repository root:

```bash
PYTHONPATH=src python3 -m nijsetu.web
```

```bash
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. Vite proxies `/api` to the Python server on port 8765. `npm run preview` uses the same proxy. For a production build, the Python server serves `frontend/dist` and supports direct dashboard routes.

## Judges’ workflow

1. Open **Batch intake**. The bundled demo dataset is queued by default; **Load sample batch** restores it at any time.
2. Use **browse files**, drag and drop, or **Choose dataset folder** for custom CSV, TSV, JSON/FHIR, TXT or ZIP datasets. Selecting uploads replaces the demo queue. ZIP members and folder-relative paths are preserved. Limits: 32 MiB total expanded content and 500 files per batch. Nested ZIPs are unsupported inputs.
3. Choose Balanced or Strict. Optionally declare a **source system / hospital** if all uploaded files share one identifier issuer. Leaving it blank preserves the engine’s conservative per-file issuer scoping; explicit FHIR identifier systems still work.
4. Click **Run sample batch** or **Run uploaded batch**. Real parsing and content checks determine file dispositions. Unsupported or malformed inputs receive reason codes; a batch with no releasable records is blocked.
5. Open **Review patient links**, search/filter actual records, and inspect rule evidence and file accounting. Identity holds are unmerged; sanitization may still release them independently. Sanitization holds and quarantined files stay out of the release.
6. Acknowledge **I’ve reviewed the records and all exclusions** and click **Confirm review**.
7. Open **View release checks**, then **Download validated release**. The ZIP contains sanitized files under `released/`, `report.json`, `hold_index.json` and `manifest.json`. The API requires review, successful engine publication and successful independent validation, then revalidates the archive before download.

Changing the queue, source system or policy invalidates the current run and review. Run history and safe JSON summaries reflect actual engine results.

## Local data lifecycle

The API binds only to 127.0.0.1. It makes no cloud calls and uses no external models, analytics or hosted fonts. Upload contents are read and sent to the local server only when a run starts. A server-generated master key stays in server memory. Each run has a new release ID, so release tokens rotate.

Inputs reside in an owner-restricted temporary workspace and are deleted after processing. Private review evidence and validated releases remain in server memory/local temporary storage for the session. Completed runs older than two hours are removed when a new run is created. The server permits at most 20 retained runs and two active/queued runs. Restarting the server clears runs and removes its workspace on normal shutdown. Abrupt process termination can leave the OS temporary directory behind. Browser state is memory-only and clears on refresh; there are no patient-data writes to browser storage. This is a local research prototype, not a hosted multi-user service.

## API

| Endpoint                      | Purpose                                                     |
| ----------------------------- | ----------------------------------------------------------- |
| `GET /api/health`             | Engine availability                                         |
| `GET /api/options`            | Python policy presets, actual demo file metadata and limits |
| `POST /api/runs`              | Start demo or uploaded dataset processing                   |
| `GET /api/runs/{id}`          | Poll actual stage/progress, results and safe error codes    |
| `POST /api/runs/{id}/review`  | Record explicit operator acknowledgement                    |
| `GET /api/runs/{id}/download` | Revalidate and download the public release ZIP              |

Run creation accepts `{ "demo": true, "policy": "balanced" }` or `{ "files": [{ "name": "patients.csv", "content": "<base64>" }], "policy": "strict", "source_system": "OPTIONAL_ISSUER" }`. Review requires `{ "acknowledged": true }`. Filenames are validated, duplicate/conflicting paths are rejected, ZIP expansion is bounded, and ZIP path traversal/symlinks/encryption are rejected. Run IDs are opaque per-session capabilities; the local API rejects nonlocal origins and hostnames.

## Verification

From the repository root:

```bash
.venv/bin/python -m pytest tests/test_web.py -q
```

From `frontend/`:

```bash
npm run build
npm test
npm run test:live
```

Playwright starts both the Python API and Vite and uses installed Google Chrome. Its Python command expects the repository `.venv`; create/install that environment first or adjust `playwright.config.ts`. Tests cover actual demo and uploaded data, release gating, blocked malformed inputs, connection errors/retry, policy invalidation, search, mobile navigation, populated layouts, local fonts and routing. API tests inspect and independently validate release archives, check PHI removal/clinical preservation, verify ZIP handling and reject unsafe requests.

## Source structure

`src/Workspace.tsx` owns session state and real API requests; `src/api.ts` defines the API types/client. Pages in `src/pages/` render engine results. `src/data/demo.ts` supplies the landing animation and offline initial display defaults; processing uses server-owned demo fixtures under `src/nijsetu/demo_data/`. The API implementation is `src/nijsetu/web.py` at the repository root.

The visual theme, supplied logo and locally bundled fonts are preserved. Font notices are in `public/fonts/*-LICENSE.txt`.
