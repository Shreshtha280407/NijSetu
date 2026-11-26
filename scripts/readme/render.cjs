/* Code-native artwork, using the real captures produced by capture.cjs. */
const fs = require('node:fs');
const path = require('node:path');
const os = require('node:os');
const { pathToFileURL } = require('node:url');
const { execFileSync } = require('node:child_process');
const { chromium } = require('../../frontend/node_modules/@playwright/test');
const root = path.resolve(__dirname, '../..');
const out = path.join(root, 'docs/assets/readme');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'nijsetu-artwork-'));
const ffmpeg = (args) => execFileSync('ffmpeg', ['-hide_banner', '-loglevel', 'error', '-y', ...args]);
const esc = (s) => String(s).replaceAll('&', '&amp;').replaceAll('<', '&lt;');
const text = (x, y, s, size = 24, color = '#333052', weight = 400) => `<text x="${x}" y="${y}" font-size="${size}" fill="${color}" font-weight="${weight}">${esc(s)}</text>`;
const arrow = (d, dashed = false, color = '#77728e') => `<path d="${d}" fill="none" stroke="${color}" stroke-width="3" ${dashed ? 'stroke-dasharray="9 7"' : ''} marker-end="url(#arrow)"/>`;
const box = (x, y, w, h, title, lines = [], tone = 'light', label = '') => {
  const dark = tone === 'dark';
  return `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="20" fill="${dark ? '#30294f' : tone === 'gold' ? '#f9e8cc' : '#fffaf3'}" stroke="${dark ? '#635779' : '#ded2c5'}" stroke-width="2"/>`
    + (label ? text(x + 25, y + 34, label, 18, dark ? '#edbb7a' : '#8b6136', 700) : '')
    + text(x + 25, y + (label ? 76 : 45), title, 28, dark ? '#fff7e9' : '#30294f', 600)
    + lines.map((s, i) => text(x + 25, y + (label ? 114 : 86) + i * 34, s, Math.min(23, (w - 50) / (s.length * 0.53)), dark ? '#d4cbdf' : '#625d72')).join('');
};
const svg = (height, label, title, subtitle, content) => `<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="${height}" viewBox="0 0 1600 ${height}" role="img" aria-label="${esc(title)}">
<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse"><path d="M0 1 L9 5 L0 9" fill="none" stroke="#77728e" stroke-width="1.6"/></marker></defs>
<rect width="1600" height="${height}" rx="26" fill="#f5ecdf"/>
<g font-family="Arial,Helvetica,sans-serif">${text(64, 60, `NIJSETU  /  ${label}`, 19, '#8c633c', 700)}${text(64, 118, title, 44, '#30294f', 600)}${text(64, 161, subtitle, 24, '#686173')}${content}${text(64, height - 30, 'OFFLINE ENGINE  ·  CPU ONLY  ·  SOURCE-AWARE VALIDATION', 17, '#83798c', 600)}</g></svg>`;

fs.mkdirSync(out, { recursive: true });
fs.writeFileSync(path.join(out, 'workflow.svg'), svg(770, 'THE RELEASE JOURNEY', 'From fragmented records to a checked release', 'The Python pipeline links evidence, transforms identity, and checks what it publishes.',
  box(64, 218, 330, 215, 'Ingest + detect', ['CSV / TSV / JSON / TXT', 'Supported FHIR resources', 'Content + hidden surfaces'], 'light', '01  /  UNDERSTAND')
  + box(446, 218, 330, 215, 'Resolve patients', ['Scoped identifiers', 'Cross-script name evidence', 'Conflicts veto a merge'], 'light', '02  /  CORROBORATE')
  + box(828, 218, 330, 215, 'Apply policy', ['Release-scoped HMACs', 'Age bands + date offsets', 'Private staging directory'], 'light', '03  /  TRANSFORM')
  + box(1210, 218, 326, 215, 'Validate', ['Residual leak scans', 'Clinical invariants', 'FHIR + output structure'], 'dark', '04  /  CHECK')
  + arrow('M394 325 H436') + arrow('M776 325 H818') + arrow('M1158 325 H1200')
  + box(64, 520, 580, 160, 'Hold or quarantine', ['Malformed, unsupported or unresolved units', 'Reason codes make exclusions inspectable'], 'gold')
  + box(828, 520, 708, 160, 'Publish only checked output', ['Internal CLI: release-wide blocking on residual leaks', 'Organizer: attributable leaking files are quarantined'], 'light')
  + arrow('M230 433 V505', true) + arrow('M610 433 V482 H460 V505', true)
  + arrow('M1373 433 V505')
));

fs.writeFileSync(path.join(out, 'architecture.svg'), svg(1130, 'SYSTEM ARCHITECTURE', 'One local engine. Two output contracts.', 'The dashboard is a separate synthetic demonstration; an API adapter remains future work.',
  box(64, 220, 345, 180, 'Python CLI', ['analyze / sanitize / validate', 'Local key file + release ID'], 'light', 'IMPLEMENTED ENTRY POINT')
  + box(64, 445, 345, 180, 'Organizer adapter', ['main.py + RAKSHA_SECRET', 'Contract + locator verification'], 'light', 'IMPLEMENTED ENTRY POINT')
  + `<rect x="470" y="213" width="620" height="500" rx="24" fill="#e9e1f0" stroke="#c9bad6" stroke-width="2"/>`
  + text(500, 257, 'NIJSETU ENGINE  /  src/nijsetu', 21, '#6a567f', 700)
  + box(500, 280, 560, 112, 'Ingest + PII detection', ['Adapters, gazetteer, bounded encoded-payload scan'])
  + box(500, 421, 560, 112, 'Identity resolution', ['Candidate evidence, scoped IDs, contradiction vetoes'])
  + box(500, 562, 560, 112, 'Privacy + validation', ['Policy, HMAC tokens, staging, leak + clinical checks'])
  + arrow('M780 392 V412') + arrow('M780 533 V553')
  + arrow('M409 310 H455') + arrow('M409 535 H455')
  + box(1150, 220, 386, 205, 'Internal release', ['released/ + manifest.json', 'report.json + hold_index.json', 'Standalone validate command'], 'dark', 'SANITIZE CONTRACT')
  + box(1150, 476, 386, 205, 'Organizer export', ['sanitized/ safe-path mirror', 'report.json: raksha-report/1', 'records.jsonl: exact locators'], 'dark', 'SECRET WARDS CONTRACT')
  + arrow('M1090 320 H1138') + arrow('M1090 580 H1138')
  + `<path d="M64 771 H1536" stroke="#d0c0b6" stroke-width="2" stroke-dasharray="10 8"/>`
  + box(64, 824, 550, 186, 'React dashboard', ['Intake → review → synthetic preview', 'Fixtures + session memory; no engine calls'], 'gold', 'INTERACTIVE SYNTHETIC DEMO')
  + box(960, 824, 576, 186, 'Loopback API adapter', ['Planned connection to the existing engine', 'Not implemented in this checkout'], 'light', 'FUTURE INTEGRATION')
  + arrow('M614 920 H943', true) + text(675, 895, 'future connection', 22, '#83758c')
));

const file = (relative) => pathToFileURL(path.join(root, relative)).href;
const img = (name) => file(`docs/assets/readme/${name}.webp`);
const fonts = `@font-face{font-family:Fraunces;src:url('${file('frontend/public/fonts/fraunces-600.ttf')}')}@font-face{font-family:Inter;src:url('${file('frontend/public/fonts/inter-400.ttf')}')}@font-face{font-family:Inter;src:url('${file('frontend/public/fonts/inter-600.ttf')}');font-weight:600}`;
const baseStyle = `${fonts}*{box-sizing:border-box}body{margin:0;font-family:Inter,sans-serif}h1,h2,p{margin:0}`;
const hero = `<!doctype html><meta charset="utf-8"><style>${baseStyle}
body{width:1600px;height:850px;background:#1c1834;color:#fff6e9;overflow:hidden}.glow{position:absolute;inset:0;background:radial-gradient(ellipse at 79% 24%,#78608b68,transparent 56%),radial-gradient(ellipse at 12% 98%,#ba754330,transparent 53%)}.grid{position:absolute;inset:0;opacity:.13;background-image:linear-gradient(#c2a7d3 1px,transparent 1px),linear-gradient(90deg,#c2a7d3 1px,transparent 1px);background-size:64px 64px;mask-image:linear-gradient(transparent,#000)}header{position:absolute;top:58px;left:72px;right:72px;display:flex;justify-content:space-between;font-size:19px;letter-spacing:4px}.brand{font-weight:600;font-size:28px;letter-spacing:2px}.badge{border:1px solid #a68c6955;padding:10px 18px;border-radius:40px;color:#e0bc8c;letter-spacing:2px;font-size:15px}.copy{position:absolute;left:72px;top:190px;width:600px}.eyebrow{font-size:16px;letter-spacing:3px;color:#e6b780;margin-bottom:30px}h1{font-family:Fraunces,serif;font-size:76px;font-weight:600;line-height:1.11;letter-spacing:-2px}h1 span{color:#efc18e}p{font-size:23px;line-height:1.65;color:#c6bfd5;margin-top:25px;max-width:520px}.frame{position:absolute;left:705px;top:198px;width:835px;border-radius:15px;overflow:hidden;box-shadow:0 35px 85px #0007;border:1px solid #e3c9a953;background:#f3e7d6;transform:perspective(1800px) rotateY(-5deg) rotateX(2deg)}.bar{height:37px;background:#30294f;display:flex;align-items:center;gap:7px;padding:0 16px}.dot{width:7px;height:7px;border-radius:50%;background:#dcb58a}.url{color:#d4c8df;font-size:11px;margin:auto}.screen{position:relative;height:580px}.screen img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;object-position:top;opacity:0}.screen img:first-child{opacity:1}.chapter{position:absolute;left:712px;top:155px;color:#e3c4a0;font-size:14px;letter-spacing:2px}footer{position:absolute;left:72px;bottom:43px;display:flex;gap:48px;font-size:14px;letter-spacing:2px;color:#d0c3db}footer b{color:#edbb7a;font-weight:400;margin-right:10px}.rail{position:absolute;left:72px;bottom:91px;width:500px;height:2px;background:#d2b69530}.rail i{display:block;height:2px;background:#e3b480;width:30%}.scope{position:absolute;right:70px;bottom:42px;font-size:13px;color:#a99cb9;letter-spacing:1px}
</style><div class="glow"></div><div class="grid"></div><header><div class="brand">NijSetu<span style="color:#d9a96f">.</span></div><div class="badge">OFFLINE · CPU ONLY · OPEN SOURCE</div></header><div class="copy"><div class="eyebrow">A SAFER BRIDGE TO RESEARCH</div><h1>Many records.<br>One patient.<br><span>Safely shared.</span></h1><p>Link the evidence. Protect identity.<br>Preserve the clinical story.</p></div><div class="chapter">01 / UNDERSTAND THE BATCH</div><div class="frame"><div class="bar"><i class="dot"></i><i class="dot"></i><i class="dot"></i><span class="url">NijSetu / synthetic workspace</span></div><div class="screen">${['intake','review-held','release'].map(n=>`<img src="${img(n)}">`).join('')}</div></div><div class="rail"><i></i></div><footer><span><b>01</b> INGEST</span><span><b>02</b> LINK</span><span><b>03</b> VALIDATE</span></footer><div class="scope">ACTUAL UI CAPTURES · SYNTHETIC DATA</div>`;

(async () => {
  const browser = await chromium.launch({ channel: 'chrome', headless: true });
  try {
    const page = await browser.newPage({ viewport: { width: 1600, height: 850 }, deviceScaleFactor: 1 });
    const heroFile = path.join(work, 'hero.html'); fs.writeFileSync(heroFile, hero);
    await page.goto(pathToFileURL(heroFile).href); await page.evaluate(() => document.fonts.ready);
    for (let frame = 0; frame < 72; frame++) {
      await page.evaluate((frame) => {
        const scene = Math.floor(frame / 24), local = frame % 24;
        const labels = ['01 / UNDERSTAND THE BATCH', '02 / FOLLOW THE EVIDENCE', '03 / REVIEW BEFORE RELEASE'];
        document.querySelector('.chapter').textContent = labels[scene];
        document.querySelectorAll('.screen img').forEach((im, i) => {
          im.style.opacity = i === scene ? Math.min(1, local / 4) : i === (scene + 2) % 3 ? 1 - Math.min(1, local / 4) : 0;
        });
        document.querySelector('.frame').style.translate = `0 ${-Math.sin(frame / 72 * Math.PI * 2) * 5}px`;
        document.querySelector('.rail i').style.width = `${15 + frame / 72 * 85}%`;
      }, frame);
      await page.screenshot({ path: path.join(work, `hero-${String(frame).padStart(3, '0')}.png`) });
    }
    ffmpeg(['-i', path.join(work, 'hero-012.png'), '-c:v', 'libwebp', '-quality', '90', path.join(out, 'hero-poster.webp')]);
    ffmpeg(['-framerate', '6', '-i', path.join(work, 'hero-%03d.png'), '-vf', 'scale=1200:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=128[p];[b][p]paletteuse=dither=bayer:bayer_scale=4', '-loop', '0', path.join(out, 'hero.gif')]);
    const gallery = `<!doctype html><style>${baseStyle}body{background:#f5ecdf;padding:50px 54px;color:#30294f}.eyebrow{color:#8c633c;font-size:17px;letter-spacing:3px}h1{font-family:Fraunces;font-size:46px;margin:16px 0 30px}.gallery{display:grid;grid-template-columns:1fr 1fr;gap:28px}.tile{background:#fffaf3;border:1px solid #d8cbbd;border-radius:16px;overflow:hidden}.label{padding:18px 22px;font-size:20px}.label span{color:#9b6b35;margin-right:15px}img{display:block;width:100%;border-top:1px solid #ded2c5}</style><div class="eyebrow">NIJSETU / INSIDE THE WORKSPACE</div><h1>Every step, in the open.</h1><div class="gallery">${[['intake','01','Prepare the batch'],['review-held','02','Understand an uncertain link'],['release','03','Inspect the synthetic preview'],['policy','04','Compare privacy presets']].map(([n,k,l])=>`<div class="tile"><div class="label"><span>${k}</span>${l}</div><img src="${img(n)}"></div>`).join('')}</div>`;
    const galleryFile = path.join(work, 'gallery.html'); fs.writeFileSync(galleryFile, gallery);
    await page.setViewportSize({ width: 1600, height: 1430 });
    await page.goto(pathToFileURL(galleryFile).href); await page.evaluate(() => document.fonts.ready);
    const bounds = await page.locator('.gallery').boundingBox();
    await page.screenshot({ path: path.join(work, 'gallery.png'), clip: { x: 0, y: 0, width: 1600, height: Math.ceil(bounds.y + bounds.height + 50) } });
    ffmpeg(['-i', path.join(work, 'gallery.png'), '-c:v', 'libwebp', '-quality', '88', path.join(out, 'workspace-gallery.webp')]);
    for (const name of ['architecture', 'workflow']) {
      await page.setViewportSize({ width: 1600, height: name === 'architecture' ? 1130 : 770 });
      await page.goto(file(`docs/assets/readme/${name}.svg`));
      await page.screenshot({ path: path.join(work, `${name}-preview.png`) });
    }
    console.log('Rendered hero, gallery, architecture and workflow:', out);
    console.log('Diagram inspection images:', work);
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exitCode = 1; });
