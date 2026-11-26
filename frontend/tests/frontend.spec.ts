import { expect, test } from '@playwright/test';

test('landing keeps the reference theme and navigation works', async ({ page }) => {
  const failures: string[] = [];
  page.on('pageerror', (error) => failures.push(error.message));
  await page.goto('/');
  await expect(
    page.getByRole('heading', { name: 'Many records. One patient. Safely shared.' }),
  ).toBeVisible();
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgba(0, 0, 0, 0)');
  await expect(page.locator('html')).toHaveCSS('background-color', 'rgb(243, 231, 214)');
  await page.getByRole('link', { name: 'How it works', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'One batch, four gates' })).toBeInViewport();
  await page.getByRole('link', { name: 'Trust and privacy', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Built to stay on your machine' }),
  ).toBeInViewport();
  await page.getByRole('link', { name: 'Launch dashboard', exact: true }).first().click();
  await expect(page.getByRole('heading', { name: 'Good data. Safer beginnings.' })).toBeVisible();
  expect(failures).toEqual([]);
});

test('full synthetic intake, review, release and download flow', async ({ page }) => {
  await page.goto('/dashboard/intake');
  await expect(page.getByText('scan_0042.pdf', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'Quarantined (1)', exact: true }).click();
  await expect(page.getByText('registration.csv', { exact: true })).not.toBeVisible();
  await expect(page.getByText('scan_0042.pdf', { exact: true })).toBeVisible();
  await page.getByRole('button', { name: 'All files (5)', exact: true }).click();
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'One person. A clearer picture.' })).toBeVisible();
  await page.getByRole('link', { name: 'Release checks' }).first().click();
  await expect(page.getByRole('button', { name: 'Download validated release' })).toBeDisabled();
  await page.getByRole('link', { name: 'Review batch results' }).click();
  await page.getByRole('button', { name: 'Held (1)', exact: true }).click();
  await page.getByRole('button', { name: /Arif opd_note.txt/ }).click();
  await expect(
    page.getByText('Insufficient or conflicting evidence', { exact: true }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Confirm review' })).toBeDisabled();
  await page
    .getByRole('checkbox', { name: 'I’ve reviewed the records and all exclusions.' })
    .check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  await page.getByRole('link', { name: 'View release checks' }).click();
  await expect(
    page.getByRole('heading', { name: 'Protected identity. Preserved medicine.' }),
  ).toBeVisible();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download validated release' }).click();
  const download = await downloadEvent;
  expect(download.suggestedFilename()).toMatch(/^nijsetu-[a-f0-9]+\.zip$/);
  const stream = await download.createReadStream();
  const chunks = [];
  for await (const chunk of stream!) chunks.push(chunk);
  const archive = Buffer.concat(chunks);
  expect(archive.subarray(0, 2).toString()).toBe('PK');
  await page.getByRole('link', { name: 'Run history' }).click();
  await expect(page.getByRole('cell', { name: 'Review complete' })).toBeVisible();
  await page.screenshot({ path: 'test-results/history-desktop.png', fullPage: true });
});

test('changing policy invalidates previous review and updates the sample output', async ({
  page,
}) => {
  await page.goto('/dashboard/intake');
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  await page.getByRole('link', { name: 'Privacy policy', exact: true }).click();
  await page.getByRole('radio', { name: 'Strict policy', exact: true }).check();
  await expect(page.getByText('Strict selected', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: 'Release checks' }).first().click();
  await expect(
    page.getByRole('heading', { name: 'Let’s prepare a release worth trusting.' }),
  ).toBeVisible();
  await page.getByRole('link', { name: 'Go to batch intake' }).click();
  await expect(page.getByRole('radio', { name: /Strict/ })).toBeChecked();
  await expect(page.getByText('±45 days', { exact: true })).toBeVisible();
});

test('uploaded datasets run through the real engine and never show demo records', async ({
  page,
}) => {
  await page.goto('/dashboard/intake');
  await page.getByLabel('Choose batch files').setInputFiles([
    {
      name: 'judge-patients.csv',
      mimeType: 'text/csv',
      buffer: Buffer.from('Patient Name,UHID,BP\nMeera Sharma,JUDGE001,120/80'),
    },
    {
      name: 'scan.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('synthetic unsupported format'),
    },
  ]);
  await expect(page.getByText('judge-patients.csv', { exact: true })).toBeVisible();
  await expect(page.getByText('registration.csv', { exact: true })).not.toBeVisible();
  await page.getByRole('button', { name: 'Run uploaded batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await expect(page.getByRole('button', { name: /Meera Sharma judge-patients.csv/ })).toBeVisible();
  await expect(page.getByText('Mohammad Arif', { exact: true })).not.toBeVisible();
  await expect(page.getByText('UNSUPPORTED_EXTENSION', { exact: true }).first()).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  await page.getByRole('link', { name: 'View release checks' }).click();
  await expect(page.getByRole('button', { name: 'Download validated release' })).toBeEnabled();
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download validated release' }).click();
  expect((await downloadEvent).suggestedFilename()).toMatch(/\.zip$/);
});

test('malformed dataset shows real parsing reasons and cannot download', async ({ page }) => {
  await page.goto('/dashboard/intake');
  await page.getByLabel('Choose batch files').setInputFiles({
    name: 'broken.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{bad json'),
  });
  await page.getByRole('button', { name: 'Run uploaded batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await expect(page.getByText('JSON_SYNTAX_ERROR', { exact: true }).first()).toBeVisible();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  await page.getByRole('link', { name: 'View release checks' }).click();
  await expect(
    page.getByRole('heading', { name: 'Publication blocked by the engine.' }),
  ).toBeVisible();
  await expect(page.getByRole('button', { name: 'Download validated release' })).toBeDisabled();
});

test('backend connection failures show actionable errors and allow retry', async ({ page }) => {
  await page.route('**/api/**', (route) => route.abort());
  await page.goto('/dashboard/intake');
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('Start python -m nijsetu.web');
  await expect(page.getByRole('button', { name: 'Run sample batch', exact: true })).toBeEnabled();
  await page.unroute('**/api/**');
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await expect(page.getByRole('link', { name: 'Review patient links' })).toBeVisible();
});

test('record search and filters have a useful empty state', async ({ page }) => {
  await page.goto('/dashboard/intake');
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await page.getByRole('textbox', { name: 'Search records' }).fill('no-such-patient');
  await expect(page.getByText('No matching records')).toBeVisible();
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.getByRole('button', { name: /Mohammad Arif registration.csv/ })).toBeVisible();
});

test('mobile menus, all routes and layouts work without page overflow', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open menu', exact: true }).click();
  await page.getByRole('link', { name: 'Features', exact: true }).click();
  await expect(
    page.getByRole('heading', { name: 'Protect the person, keep the medicine' }),
  ).toBeInViewport();
  await page.goto('/dashboard/intake');
  await page.screenshot({ path: 'test-results/intake-mobile.png', fullPage: true });
  await page.getByRole('button', { name: 'Open navigation' }).click();
  await page.getByRole('link', { name: 'Privacy policy', exact: true }).click();
  await expect(page.getByRole('heading', { name: 'Privacy with a purpose.' })).toBeVisible();
  await page.getByRole('radio', { name: 'Strict policy', exact: true }).check();
  await page.getByRole('link', { name: 'Back to batch intake' }).click();
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  await page.getByRole('link', { name: 'View release checks' }).click();
  await expect(page.getByRole('button', { name: 'Download validated release' })).toBeEnabled();
  await page.screenshot({ path: 'test-results/release-mobile.png', fullPage: true });
  for (const route of [
    '/',
    '/dashboard/intake',
    '/dashboard/review',
    '/dashboard/release',
    '/dashboard/policy',
    '/dashboard/history',
  ]) {
    await page.goto(route);
    const overflows = await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    );
    expect(overflows, `${route} should fit mobile width`).toBe(false);
  }
});

test('fonts and application work without any external network requests', async ({ page }) => {
  const externalRequests: string[] = [];
  await page.route('**/*', async (route) => {
    if (!route.request().url().startsWith('http://127.0.0.1:5173')) {
      externalRequests.push(route.request().url());
      return route.abort();
    }
    await route.continue();
  });
  await page.goto('/');
  await page.evaluate(async () => {
    // Browsers load font weights on demand. Exercise every bundled face explicitly.
    await Promise.all(
      [
        '600 24px Fraunces',
        '700 24px Fraunces',
        '400 16px Inter',
        '500 16px Inter',
        '600 16px Inter',
        '500 16px "Plus Jakarta Sans"',
        '600 16px "Plus Jakarta Sans"',
        '700 16px "Plus Jakarta Sans"',
        '800 16px "Plus Jakarta Sans"',
        '600 16px "Noto Sans Devanagari"',
        '700 16px "Noto Sans Devanagari"',
      ].map((font) => document.fonts.load(font)),
    );
    await document.fonts.ready;
  });
  expect(await page.evaluate(() => document.fonts.check('700 24px Fraunces'))).toBe(true);
  expect(await page.evaluate(() => document.fonts.check('500 16px Inter'))).toBe(true);
  await page.getByRole('link', { name: 'Launch dashboard' }).first().click();
  await expect(page.getByRole('heading', { name: 'Good data. Safer beginnings.' })).toBeVisible();
  expect(externalRequests).toEqual([]);
});

test('unknown routes and direct dashboard entry have clear destinations', async ({ page }) => {
  await page.goto('/dashboard');
  await expect(page).toHaveURL(/\/dashboard\/intake$/);
  await page.goto('/missing-page');
  await expect(page.getByRole('heading', { name: 'Let’s get you back.' })).toBeVisible();
  await page.getByRole('link', { name: 'Open workspace' }).click();
  await expect(page).toHaveURL(/\/dashboard\/intake$/);
});

test('populated pages fit small phones, tablets and desktop screens', async ({
  page,
}, testInfo) => {
  test.setTimeout(120_000);
  await page.goto('/dashboard/intake');
  await page.getByRole('button', { name: 'Run sample batch', exact: true }).click();
  await page.getByRole('link', { name: 'Review patient links' }).click();
  await page.getByRole('checkbox').check();
  await page.getByRole('button', { name: 'Confirm review' }).click();
  const screens = [320, 375, 768, 1024, 1280, 1440];
  const routes = [
    { label: 'Batch intake', slug: 'intake' },
    { label: 'Link review', slug: 'review' },
    { label: 'Release checks', slug: 'release' },
    { label: 'Privacy policy', slug: 'policy' },
    { label: 'Run history', slug: 'history' },
  ];
  for (const width of screens) {
    await page.setViewportSize({ width, height: 1000 });
    for (const route of routes) {
      const menu = page.getByRole('button', { name: 'Open navigation' });
      if (await menu.isVisible()) await menu.click();
      await page.locator('.sidebar').getByRole('link', { name: route.label, exact: true }).click();
      const dimensions = await page.evaluate(() => ({
        page: document.documentElement.scrollWidth,
        viewport: window.innerWidth,
      }));
      expect
        .soft(dimensions.page, `${route.slug} at ${width}px`)
        .toBeLessThanOrEqual(dimensions.viewport);
      if (width === 375 || width === 1440) {
        const file = testInfo.outputPath(`${route.slug}-${width}.png`);
        await page.screenshot({ path: file, fullPage: true });
        await testInfo.attach(`${route.label} · ${width}px`, {
          path: file,
          contentType: 'image/png',
        });
      }
    }
    const menu = page.getByRole('button', { name: 'Open navigation' });
    if (await menu.isVisible()) await menu.click();
    await page.getByRole('link', { name: 'Back to home', exact: true }).click();
    expect
      .soft(
        await page.evaluate(() => document.documentElement.scrollWidth),
        `landing at ${width}px`,
      )
      .toBeLessThanOrEqual(width);
    await page.getByRole('link', { name: 'Launch dashboard', exact: true }).first().click();
    await expect(page.getByRole('heading', { name: 'Good data. Safer beginnings.' })).toBeVisible();
  }
});
