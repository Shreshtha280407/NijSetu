import { execFileSync } from 'node:child_process';
import { resolve } from 'node:path';
import { expect, test } from '@playwright/test';
import type { EngineRun } from '../src/api';

// Real Vite -> Python requests throughout: no mocked API responses.
test('frontend integrates with the real backend for uploads and demo data', async ({
  page,
}, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', (error) => errors.push(error.message));
  await page.goto('/dashboard/intake');
  await expect(page.getByText('Engine connected', { exact: true })).toBeVisible();

  const csv = 'Patient Name,UHID,Phone,BP\nMeera Sharma,JUDGE001,9876543210,120/80';
  await page.getByLabel('Choose batch files').setInputFiles([
    { name: 'judge-patients.csv', mimeType: 'text/csv', buffer: Buffer.from(csv) },
    {
      name: 'judge-visit.json',
      mimeType: 'application/json',
      buffer: Buffer.from(
        JSON.stringify({ patientName: 'Meera Sharma', uhid: 'JUDGE001', hba1c: '6.4%' }),
      ),
    },
    {
      name: 'scan.pdf',
      mimeType: 'application/pdf',
      buffer: Buffer.from('unsupported synthetic scan'),
    },
  ]);
  await page.getByRole('textbox', { name: 'Source system', exact: true }).fill('JUDGE_HOSPITAL');
  await page.getByRole('radio', { name: /Strict/ }).check();

  const evidence = [];
  for (const demo of [false, true]) {
    await test.step(
      demo
        ? 'Process the built-in demo through the same backend'
        : 'Process uploaded dataset through the backend',
      async () => {
        if (demo) {
          await page
            .locator('.sidebar')
            .getByRole('link', { name: 'Batch intake', exact: true })
            .click();
          await page.getByRole('button', { name: 'Load sample batch', exact: true }).click();
        }
        const createdResponse = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === '/api/runs' &&
            response.request().method() === 'POST',
        );
        await page
          .getByRole('button', {
            name: demo ? 'Run sample batch' : 'Run uploaded batch',
            exact: true,
          })
          .click();
        const response = await createdResponse;
        expect(response.status()).toBe(202);
        const payload = response.request().postDataJSON();
        expect(payload.policy).toBe('strict');
        if (demo) {
          expect(payload.demo).toBe(true);
          expect(payload.files).toBeUndefined();
        } else {
          expect(payload.source_system).toBe('JUDGE_HOSPITAL');
          expect(
            Buffer.from(
              payload.files.find((file: { name: string }) => file.name === 'judge-patients.csv')
                .content,
              'base64',
            ).toString(),
          ).toBe(csv);
        }
        const created = (await response.json()) as EngineRun;
        await page.getByRole('link', { name: 'Review patient links', exact: true }).click();
        const backendResponse = await page.request.get(`/api/runs/${created.id}`);
        expect(backendResponse.status()).toBe(200);
        const run = (await backendResponse.json()) as EngineRun;
        expect(run.state).toBe('complete');
        expect(run.demo).toBe(demo);
        expect(run.result?.release_ready).toBe(true);
        expect(run.result?.checks.every((check) => check.passed)).toBe(true);
        expect(run.result?.report.hold_summary?.UNSUPPORTED_EXTENSION).toBe(1);
        expect(run.result?.report.counts?.records_released).toBe(demo ? 4 : 2);
        if (!demo) {
          expect(run.result?.analysis.counts.patients_accepted_clusters).toBe(1);
          expect(run.result?.records).toHaveLength(2);
          expect(
            run.result?.records.every(
              (record) => record.status === 'ACCEPTED' && record.name === 'Meera Sharma',
            ),
          ).toBe(true);
          await expect(
            page.getByRole('button', { name: /Meera Sharma judge-patients.csv/ }),
          ).toBeVisible();
          await expect(page.getByText('Mohammad Arif', { exact: true })).not.toBeVisible();
        }
        await expect(
          page.getByText('UNSUPPORTED_EXTENSION', { exact: true }).first(),
        ).toBeVisible();

        await page
          .locator('.sidebar')
          .getByRole('link', { name: 'Release checks', exact: true })
          .click();
        await expect(
          page.getByRole('button', { name: 'Download validated release' }),
        ).toBeDisabled();
        const locked = await page.request.get(`/api/runs/${run.id}/download`);
        expect(locked.status()).toBe(409);
        await page.getByRole('link', { name: 'Review batch results' }).click();
        await page
          .getByRole('checkbox', { name: 'I’ve reviewed the records and all exclusions.' })
          .check();
        const reviewedResponse = page.waitForResponse(
          (response) =>
            new URL(response.url()).pathname === `/api/runs/${run.id}/review` &&
            response.request().method() === 'POST',
        );
        await page.getByRole('button', { name: 'Confirm review', exact: true }).click();
        const reviewed = await reviewedResponse;
        expect(reviewed.status()).toBe(200);
        expect((await reviewed.json()).reviewed).toBe(true);
        await page.getByRole('link', { name: 'View release checks', exact: true }).click();
        await expect(
          page.getByRole('button', { name: 'Download validated release' }),
        ).toBeEnabled();

        const downloaded = page.waitForEvent('download');
        const downloadResponse = page.waitForResponse(
          (response) => new URL(response.url()).pathname === `/api/runs/${run.id}/download`,
        );
        await page.getByRole('button', { name: 'Download validated release', exact: true }).click();
        const download = await downloaded;
        expect(download.suggestedFilename()).toBe(`nijsetu-${run.id}.zip`);
        expect((await downloadResponse).status()).toBe(200);
        const stream = await download.createReadStream();
        const chunks: Buffer[] = [];
        for await (const chunk of stream!) chunks.push(Buffer.from(chunk));
        const archive = Buffer.concat(chunks);
        // Read the actual browser download and verify every manifest digest.
        const contents = JSON.parse(
          execFileSync(
            resolve('..', '.venv', 'bin', 'python'),
            [
              '-c',
              `
import hashlib, io, json, sys, zipfile
with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
    manifest = json.loads(archive.read('manifest.json'))
    assert set(archive.namelist()) == {'manifest.json'} | {entry['path'] for entry in manifest['files']}
    for entry in manifest['files']:
        data = archive.read(entry['path'])
        assert hashlib.sha256(data).hexdigest() == entry['sha256']
        assert len(data) == entry['bytes']
    print(json.dumps({
        'manifest': manifest,
        'report': json.loads(archive.read('report.json')),
        'files': archive.namelist(),
        'released': '\\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist() if name.startswith('released/')),
    }))
`,
            ],
            { input: archive, encoding: 'utf8' },
          ),
        );
        expect(contents.manifest.release_id).toBe(run.id);
        expect(contents.manifest.policy_name).toBe('strict');
        expect(contents.report).toEqual(run.result?.report);
        expect(contents.files).toContain('hold_index.json');
        expect(contents.files.some((name: string) => name.startsWith('released/'))).toBe(true);
        expect(
          contents.files.some((name: string) => /(^|\/)(input|private)(\/|$)/.test(name)),
        ).toBe(false);
        expect(contents.released).toContain(demo ? '140/90' : '120/80');
        expect(contents.released).toContain(demo ? '8.2%' : '6.4%');
        for (const identifier of [
          'Meera',
          'Sharma',
          'JUDGE001',
          '9876543210',
          'Mohammad',
          'Arif',
          'AR001',
        ]) {
          expect(contents.released).not.toContain(identifier);
        }
        await testInfo.attach(demo ? 'demo-validated-release' : 'upload-validated-release', {
          body: archive,
          contentType: 'application/zip',
        });
        evidence.push({
          demo,
          run_id: run.id,
          policy: run.policy,
          counts: run.result?.report.counts,
          checks: run.result?.checks,
          manifest_verified: true,
        });
      },
    );
  }
  expect(errors).toEqual([]);
  await testInfo.attach('integration-evidence', {
    body: JSON.stringify(evidence, null, 2),
    contentType: 'application/json',
  });
});
