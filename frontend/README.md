# NijSetu frontend

React + TypeScript + Vite frontend for the existing NijSetu Python project. The landing page follows the supplied **Landing, Saffron and Indigo (warm)** HTML. All workspace pages share the same parchment background, Fraunces headings, indigo controls, saffron accents, and raised/inset panels.

## Run locally

Requires Node.js 22.12+ and npm.

```bash
cd frontend
npm ci
npm run dev
```

Open **http://127.0.0.1:5173**. The development server binds to loopback only.

```bash
npm run build    # TypeScript check and production build → dist/
npm run preview  # Serve the production build locally
npm run format   # Format the source consistently with Prettier
```

## Pages

| Route                | Purpose                                                                        |
| -------------------- | ------------------------------------------------------------------------------ |
| `/`                  | Reference-themed landing page with animated synthetic linkage example          |
| `/dashboard/intake`  | File intake, format quarantine, sample loading, policy selection, run progress |
| `/dashboard/review`  | Patient records, search/filter, link evidence, explicit held-record exclusion  |
| `/dashboard/release` | Demo checks, clinical comparison, gated synthetic JSON download                |
| `/dashboard/policy`  | Balanced/strict presets and comparison against the Python configuration        |
| `/dashboard/history` | Session run history and downloadable demo summaries                            |

## Try the complete flow

1. Launch the dashboard from the landing page.
2. The built-in sample is already queued. Click **Run sample batch**.
3. Open **Review patient links**. Select the held OPD record to inspect its evidence.
4. Check the acknowledgement and choose **Confirm exclusion**.
5. Open **View release checks**, then **Download synthetic preview**.

Changing the policy or intake queue invalidates the current run/review. A download stays disabled until the sample run and the explicit review are complete. The held record is never merged into the patient group.

## Frontend scope

This is an interactive **synthetic demo**, not an engine integration. Progress, records, linkage evidence, and check outcomes are illustrative. Downloaded files are explicitly marked as demo output and make no claim of engine validation.

The file picker and drop zone read filenames, sizes, and extensions only. They do not read or send file contents. Adding local files replaces the built-in sample and disables simulated processing. Reload the sample to explore the demo again. CSV, TSV, JSON (including FHIR JSON), and TXT have supported intake labels; other extensions are quarantined. These labels are not content/schema validation.

State lives in React memory and clears on refresh. There are no cloud requests, analytics, authentication, browser-storage writes, or runtime font/CDN dependencies. The frontend does not invoke the Python source or CLI.

## Straightforward source structure

```text
src/
  main.tsx                    Routes, document titles and scroll handling
  Workspace.tsx               Shared queue, policy, demo run and review state
  styles.css                  Theme tokens, layouts and responsive styles
  fonts.css                   Locally hosted reference fonts
  data/demo.ts                Typed synthetic fixtures, policy display values, JSON download helper
  components/
    UI.tsx                    Brand, badges, headings, statistics and empty states
    DashboardLayout.tsx       Shared navigation, stepper, demo notice and notifications
  pages/
    Landing.tsx               Landing page matching the supplied design
    Intake.tsx                Batch intake
    Review.tsx                Link evidence and held-record review
    Release.tsx               Checks and preview download
    Policy.tsx                Policy comparison
    History.tsx               Session run history
tests/frontend.spec.ts        Chrome/Playwright end-to-end checks
reference/LandingSaffron.html  Original supplied landing HTML (reference only)
```

Theme colors and shadows are CSS custom properties at the top of `src/styles.css`. Add a page under `src/pages/` and register it in `main.tsx`; shared workspace navigation lives in `DashboardLayout.tsx`. No global state library, UI framework, or custom templating runtime is required.

The supplied logo is shared by the header, sidebar and footer through `Brand` in `components/UI.tsx`. `public/brand/nijsetu-logo.svg` wraps the original JPEG with a cropped display viewport; it is not a vector redraw. The unchanged source image is preserved as `public/brand/nijsetu-logo-original.jpeg`. The favicon uses the same mark.

## Connecting the Python engine next

The engine currently exposes a CLI, not an HTTP endpoint. Replace the simulation in `Workspace.tsx` with requests to a loopback API adapter for `analyze`/`sanitize`/`validate`. Keep the engine's actual results as the source of truth for patient groups, holds, quarantine reasons and release status. Keep master keys in the local server, not the browser. Production downloads must come from validated engine output; `Release.tsx` currently generates a clearly labeled synthetic illustration only.

Policy display values are based on `../src/nijsetu/phase2/policy.py`. When adding the adapter, return the available presets from Python to avoid maintaining two configuration sources.

## Test visibly in Google Chrome

Install Google Chrome if it is not available, then install Playwright's video helper once:

```bash
npx playwright install ffmpeg
npm run test:live
```

For a slower visible walkthrough:

```bash
PLAYWRIGHT_SLOW_MO=320 npm run test:live
```

`npm test` runs the same checks headlessly. `npm run test:report` opens the HTML report. Test videos and screenshots are written to ignored `test-results/`; failure traces are retained. The suite covers landing navigation, the full demo/download flow, release gating, policy invalidation, uploaded-file isolation, search, mobile menus, responsive overflow, local fonts, external-request blocking, direct routing and 404 recovery. Populated pages are checked at 320, 375, 768, 1024, 1280 and 1440 pixels, with phone and desktop screenshots attached to the report.

Production static hosting must fall back to `index.html` for client-side routes. Local development and preview already support those routes.

Verified on **27 November 2026** with Node.js 26.12.0 and Google Chrome:
`npm run build` passed and `npm test` passed **9/9** checks, including populated
layouts at all six documented widths. The multi-width timeout reported during
the earlier README capture did not recur; no application or browser-test
source changes were needed for this rerun. See the [verification notes](../docs/assets/readme/README.md#fresh-verification).

## Font notices

Fraunces, Inter, Plus Jakarta Sans, and Noto Sans Devanagari are bundled locally under the SIL Open Font License. Their full notices are in `public/fonts/*-LICENSE.txt`. The original reference's custom `support.js` runtime is not required by this implementation.
