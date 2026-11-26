# README media and verification

Captured locally on **27 November 2026 (Asia/Kolkata)** from the existing NijSetu frontend. The UTC timestamp, viewport, chapters and capture assertions are in [`capture-manifest.json`](capture-manifest.json).

## What the visuals show

- `hero.gif`: a 12-second designed presentation, composited from real intake, held-record and release screenshots. This is an animated cover, not a continuous recording. `hero-poster.webp` supplies its static alternative.
- `product-tour.mp4`: approximately 34 seconds of continuous Chrome capture, at 1440 × 1000, showing actual navigation and interactions in the existing synthetic demo. No audio.
- `product-tour.gif`: an accelerated preview of the recording at 5 fps and 840 pixels wide. The full-resolution MP4 retains the recorded timing.
- `workspace-gallery.webp`: a labeled four-screen composition. Individual WebP screenshots are linked from the root README.
- `architecture.svg` and `workflow.svg`: self-contained, code-generated diagrams based on the implemented Python pipeline and frontend scope. The future API adapter is explicitly marked as unimplemented.

All patient names, identifiers and clinical values in these captures are existing **synthetic demo fixtures**. The dashboard does not invoke the Python engine. Its illustrative check badges and JSON preview are not evidence of engine sanitization or validation. Capture automation dismisses transient notifications through the app's existing dismiss control; it does not replace UI content or invent results.

The README uses `<picture>` static alternatives for browsers that honor `prefers-reduced-motion`. MP4 links provide the full recording because GitHub READMEs do not reliably support inline video playback. Assets are repository-relative, with no external image-hosting dependency. The badge images alone use Shields.io.

## Reproduce the assets

Requirements: the frontend's installed npm dependencies, Google Chrome, Node.js 22.12+, FFmpeg available on `PATH`, and Playwright's video helper (`npx playwright install ffmpeg` from `frontend/`). The system FFmpeg and Playwright helper are separate requirements. All design fonts come from the existing local frontend assets; their notices remain in [`frontend/public/fonts`](../../../frontend/public/fonts/).

From the repository root, start the app:

```bash
cd frontend
npm ci
npx playwright install ffmpeg
npm run dev -- --port 5173 --strictPort
```

In a second terminal, from the repository root:

```bash
node scripts/readme/capture.cjs
node scripts/readme/render.cjs
```

Run capture before rendering: the cover and gallery use its screenshots. `README_BASE_URL` can override the capture origin. Temporary raw video and artwork frames are placed in a fresh `/tmp/nijsetu-*` directory, outside the repository. Reproduction changes capture timestamps and can vary slightly with browser timing.

The capture script checks that:

1. The built-in sample runs and opens patient-link review.
2. The name-only record is visibly held.
3. The preview download stays disabled before review.
4. Explicit exclusion unlocks the preview.
5. The downloaded JSON declares `demo: true`, has three linked sources, and excludes the name-only record.
6. Changing policy invalidates the prior run.
7. No JavaScript page errors or requests outside the local app origin occur during the capture.

These checks exercise the synthetic UI; they do not establish privacy or clinical guarantees.

## Fresh verification

The latest documentation review reran Python and browser tests on **27
November 2026**, using CPython **3.14.8**, Node.js **26.12.0**, and Google
Chrome. Existing captured media and `capture-manifest.json` retain their
original timestamps; no new recording was made for this review.

| Check | Result on 27 November 2026 |
|---|---|
| `.venv/bin/python -m pytest -q` | **457 passed**, 34.06 seconds on the documentation-review rerun. |
| `cd frontend && npm run build` | TypeScript and Vite production build passed. |
| `cd frontend && npm test` | **9 passed**, 49.1 seconds on the documentation-review rerun, including the populated multi-width test. |
| Original real README capture | Completed; all capture assertions passed, zero page errors, zero external app requests. |
| Documented CLI worked case | `analyze`: `CLEAN`; `sanitize`: `RELEASED`; `validate`: passed. Each exited 0 with the documented explicit issuer mappings. |
| Organizer example | Generated the 16-file synthetic corpus, ran `main.py`, and scored its output; all commands exited 0. |
| Synthetic corpus rescoring | 16/16 exported, 0/176 identifying entities leaked, 35/35 expected clinical values retained. |
| Repository documentation links | All 84 local file/anchor references checked across nine Markdown documents; none broken. |
| Gazetteer regeneration | Identical bundled JSON data; generated provenance matches the corrected notice. |

During the earlier README capture, the browser suite returned **8 passed,
1 failed**: the multi-width test timed out clicking an offscreen sidebar
link at `frontend/tests/frontend.spec.ts:231`. The documentation-review rerun
passed all nine tests without changing the application or browser-test source.
This records a successful rerun, not a fix or a guarantee against recurrence.

The restricted-sandbox Python run returned 454 passes and three
packaging-related failures because isolated builds could not fetch setuptools.
The complete rerun with network access passed all 457 tests. The browser suite
also needed permission to start its loopback Vite server and Chrome. These
are setup/environment requirements; engine processing remains offline.

Practice-corpus metrics in the root README are attributed to the existing [Gate 0.5 report](../../../GATE05_FINAL_REPORT.md). They were not freshly remeasured against the organizer's practice dataset during this documentation task. The synthetic-corpus rescoring above was run independently of the UI capture, through the real Python organizer entrypoint.
