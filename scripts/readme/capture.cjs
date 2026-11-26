/* Reproduce the README's real UI captures. Start frontend on :5173 first. */
const { chromium, expect } = require('../../frontend/node_modules/@playwright/test');
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { execFileSync } = require('node:child_process');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'docs/assets/readme');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'nijsetu-readme-'));
const baseURL = process.env.README_BASE_URL || 'http://127.0.0.1:5173';
const ffmpeg = (args) => execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args]);

(async () => {
  fs.mkdirSync(out, { recursive: true });
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1,
    recordVideo: { dir: work, size: { width: 1440, height: 1000 } },
  });
  const page = await context.newPage();
  const errors = [], external = [], chapters = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.route('**/*', async (route) => {
    if (!route.request().url().startsWith(baseURL + '/')) {
      external.push(route.request().url());
      return route.abort();
    }
    return route.continue();
  });
  const started = Date.now();
  const pause = (ms = 1800) => page.waitForTimeout(ms);
  const chapter = (title) => chapters.push({ title, seconds: +((Date.now() - started) / 1000).toFixed(1) });
  const capture = async (name, fullPage = false) => {
    const dismiss = page.getByRole('button', { name: 'Dismiss notification', exact: true });
    if (await dismiss.isVisible()) await dismiss.click();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: path.join(out, `${name}.webp`), type: 'png', fullPage });
    // Playwright writes PNG; encode it as an actual WebP for repository size.
    ffmpeg(['-i', path.join(out, `${name}.webp`), '-c:v', 'libwebp', '-quality', '88', path.join(work, `${name}.webp`)]);
    fs.copyFileSync(path.join(work, `${name}.webp`), path.join(out, `${name}.webp`));
  };
  try {
    chapter('Product introduction');
    await page.goto(baseURL);
    await expect(page.getByRole('heading', { name: 'Many records. One patient. Safely shared.' })).toBeVisible();
    await pause(2600);
    await capture('landing');
    await capture('landing-full', true);
    await page.getByRole('link', { name: 'How it works', exact: true }).click();
    await pause(1800);
    await page.getByRole('link', { name: 'Launch dashboard', exact: true }).first().click();
    chapter('Synthetic batch intake');
    await expect(page.getByRole('button', { name: 'Run sample batch', exact: true })).toBeVisible();
    await pause();
    await capture('intake');
    await page.getByRole('button', { name: 'Quarantined (1)', exact: true }).click();
    await pause(1200);
    await page.getByRole('button', { name: 'All files (5)', exact: true }).click();
    await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
    await page.getByRole('link', { name: 'Review patient links', exact: true }).click();
    chapter('Link evidence and an uncertain record');
    await pause();
    await capture('review-linked');
    await page.getByRole('button', { name: /Arif opd_note.txt/ }).click();
    await expect(page.getByText('A name is not enough', { exact: true })).toBeVisible();
    await pause(2200);
    await capture('review-held');
    await page.getByRole('link', { name: 'Release checks' }).first().click();
    await expect(page.getByRole('button', { name: 'Download synthetic preview' })).toBeDisabled();
    await pause(1800);
    await capture('release-locked');
    await page.getByRole('link', { name: 'Review the held record' }).click();
    await page.getByRole('button', { name: /Arif opd_note.txt/ }).click();
    await page.getByRole('checkbox', { name: 'I’ve reviewed the evidence. Exclude the name-only record.' }).check();
    await pause(800);
    await page.getByRole('button', { name: 'Confirm exclusion' }).click();
    chapter('Explicit exclusion and synthetic preview');
    await pause(1000);
    await page.getByRole('link', { name: 'View release checks' }).click();
    await pause(2200);
    await capture('release');
    await capture('release-full', true);
    const downloadEvent = page.waitForEvent('download');
    await page.getByRole('button', { name: 'Download synthetic preview' }).click();
    const download = await downloadEvent;
    const downloaded = await download.path();
    const data = JSON.parse(fs.readFileSync(downloaded, 'utf8'));
    if (!data.demo || data.linked_sources.length !== 3 || !data.excluded_sources.some((s) => s.reason === 'NAME_ONLY_NO_ANCHOR')) {
      throw new Error('Downloaded preview did not preserve its synthetic scope or exclusion');
    }
    await pause(1400);
    await page.getByRole('link', { name: 'Run history', exact: true }).click();
    await pause();
    await capture('history');
    await page.getByRole('link', { name: 'Privacy policy', exact: true }).click();
    chapter('Policy comparison');
    await pause();
    await capture('policy');
    await page.getByRole('radio', { name: 'Strict policy', exact: true }).check();
    await pause(1500);
    await page.getByRole('link', { name: 'Release checks' }).first().click();
    await expect(page.getByRole('heading', { name: 'Let’s prepare a release worth trusting.' })).toBeVisible();
    await pause(1800);
    const video = page.video();
    await context.close();
    const raw = await video.path();
    ffmpeg(['-i', raw, '-c:v', 'libx264', '-crf', '23', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', '-an', path.join(out, 'product-tour.mp4')]);
    ffmpeg(['-i', raw, '-vf', 'setpts=0.6*PTS,fps=5,scale=840:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=96[p];[b][p]paletteuse=dither=bayer:bayer_scale=4', '-loop', '0', path.join(out, 'product-tour.gif')]);
    if (errors.length || external.length) throw new Error(JSON.stringify({ errors, external }));
    fs.writeFileSync(path.join(out, 'capture-manifest.json'), JSON.stringify({
      capturedAt: new Date().toISOString(), baseURL, viewport: { width: 1440, height: 1000 },
      scope: 'Real Chrome recording of the existing React synthetic demo; no Python engine integration or real patient data.',
      chapters, pageErrors: errors, externalRequests: external,
      assertions: ['Held record excluded', 'Download gated before review', 'Downloaded JSON has demo=true', 'Three linked sources', 'Policy change invalidates run'],
      temporaryDirectory: work,
    }, null, 2) + '\n');
    console.log('Captured screenshots and verified product tour:', out);
  } finally {
    await browser.close();
  }
})().catch((e) => { console.error(e); process.exitCode = 1; });
