// Real-browser E2E against the running stack (API :8000, web :3000). Canned AI extractor; everything else is real.
const { chromium } = require('playwright');
const crypto = require('crypto'); const fs = require('fs'); const path = require('path');
const SECRET = process.env.SUPABASE_JWT_SECRET; const OUT = process.env.SHOTS || '/tmp/e2e_shots';
fs.mkdirSync(OUT, { recursive: true });
const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url');
function jwt(sub) { const h = b64({ alg: 'HS256', typ: 'JWT' }), p = b64({ sub, aud: 'authenticated', role: 'authenticated', exp: Math.floor(Date.now() / 1000) + 3600 });
  return `${h}.${p}.${crypto.createHmac('sha256', SECRET).update(`${h}.${p}`).digest('base64url')}`; }
const results = []; const check = (name, ok, extra = '') => { results.push([name, ok, extra]); console.log(`${ok ? 'PASS' : 'FAIL'}  ${name} ${extra}`); };
const uuid = () => crypto.randomUUID();

(async () => {
  const browser = await chromium.launch();
  const mk = async (user, opts = {}) => { const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1, ...opts });
    await ctx.addInitScript((t) => localStorage.setItem('ab_dev_jwt', t), jwt(user)); return ctx; };
  const errors = [];
  const ctx = await mk(uuid()); const page = await ctx.newPage();
  let armed = false; // the single expected 403 (no org yet) happens before onboarding completes
  page.on('console', (m) => { if (armed && m.type() === 'error') errors.push(m.text()); }); page.on('pageerror', (e) => errors.push(String(e)));
  page.on('dialog', (d) => d.accept('20'));

  // 1. no org yet -> redirected to onboarding
  await page.goto('http://localhost:3000/dashboard'); await page.waitForURL('**/onboarding');
  check('new user redirected to onboarding', true);
  await page.fill('input', 'Acme Restaurant Group'); await page.click('text=Create workspace'); await page.waitForURL('**/settings'); armed = true;
  check('org created, lands on settings', await page.locator('[data-testid=inbox]').innerText().then((t) => t.includes('invoices+acme-restaurant-group-')));

  // 2. settings: vendor + location
  await page.fill('[aria-label="Vendor name"]', 'Sysco'); await page.fill('[aria-label="Vendor email"]', 'ar@sysco.test'); await page.click('section:has-text("Vendors") >> text=Add');
  await page.waitForSelector('li:has-text("Sysco")'); await page.fill('[aria-label="Location name"]', 'Downtown'); await page.click('section:has-text("Locations") >> text=Add');
  await page.waitForSelector('li:has-text("Downtown")'); check('vendor and location added', true);

  // 3. contract via CSV import -> editable draft -> save
  await page.goto('http://localhost:3000/contracts');
  fs.writeFileSync('/tmp/e2e_contract.csv', 'sku,canonical_name,unit,contract_price,source_clause\nCHK-40,Chicken breast boneless skinless,LB,2.00,Sec 3.1 chicken fixed price\nOIL-EV,Extra virgin olive oil,GAL,30.00,Sec 3.2\n');
  await page.setInputFiles('[data-testid=contract-csv]', '/tmp/e2e_contract.csv'); await page.waitForSelector('text=Review before saving');
  await page.selectOption('section:has-text("Review before saving") select', { label: 'Sysco' }); await page.fill('section:has-text("Review before saving") label:has-text("Valid from") input', '2025-01-01');
  await page.fill('section:has-text("Review before saving") label:has-text("Valid to") input', '2025-12-31'); await page.fill('label:has-text("Reference") input', 'C-2025');
  await page.click('text=Save contract'); await page.waitForSelector('td:has-text("C-2025")'); check('contract imported from CSV and saved', true);

  // 4. upload invoice; watch status go live via polling
  fs.writeFileSync('/tmp/e2e-over.pdf', Buffer.from('%PDF-1.4\nFIXTURE:e2e-over'));
  await page.goto('http://localhost:3000/invoices'); await page.setInputFiles('[data-testid=file-input]', '/tmp/e2e-over.pdf');
  await page.waitForSelector('text=queued ✓'); check('upload accepted', true);
  await page.waitForSelector('td:has-text("needs review")', { timeout: 20000 }); check('worker extracted + audited invoice (status updates live)', true);
  await page.screenshot({ path: `${OUT}/invoices.png` });
  check('invoice row shows discrepancy total $20.00', (await page.locator('tr:has-text("E2E-1001")').innerText()).includes('$20.00'));

  // 5. invoice detail: highlight + approve
  await page.click('a:has-text("E2E-1001")'); await page.waitForSelector('[data-testid=discrepancy]');
  check('problem line highlighted', (await page.locator('tr[data-problem=true]').count()) === 1 && (await page.locator('tr[data-problem=false]').count()) === 1);
  check('shows contract price vs billed', (await page.locator('tr[data-problem=true]').innerText()).includes('$2.00'));
  const dtext = await page.locator('[data-testid=discrepancy]').first().innerText();
  check('discrepancy: price variance $20.00 with clause', dtext.includes('price variance') && dtext.includes('$20.00') && dtext.includes('Sec 3.1'), dtext.replace(/\n/g, ' | ').slice(0, 120));
  await page.screenshot({ path: `${OUT}/invoice-detail.png` });
  // inline edit keeps focus across keystrokes (regression for remount bug)
  await page.click('tr[data-problem=true] button:has-text("100")'); await page.locator('[aria-label="Edit qty"]').pressSequentially('101', { delay: 20 });
  check('inline edit input keeps focus while typing', (await page.locator('[aria-label="Edit qty"]').inputValue()) === '100.000101');   // qty is numeric(12,3) -> '100.000'; three keystrokes appended without losing focus
  await page.keyboard.press('Escape');
  await page.click('[data-testid=discrepancy] >> text=Approve'); await page.waitForSelector('[data-testid=discrepancy] >> text=approved'); check('discrepancy approved by human', true);

  // 6. claim: create draft, send, log credit
  await page.goto('http://localhost:3000/claims'); await page.check('section:has-text("ready to claim") input[type=checkbox]'); await page.click('text=Create claim draft');
  await page.waitForURL('**/claims/*'); await page.waitForSelector('text=Billing variance claim'); check('claim draft created with letter', (await page.locator('pre').innerText()).includes('14 calendar days'));
  const pdfSrc = await page.locator('iframe[title="Claim PDF"]').getAttribute('src'); const pdfRes = await ctx.request.get(pdfSrc);
  check('claim PDF served via signed URL', pdfRes.ok() && (await pdfRes.body()).slice(0, 4).toString() === '%PDF');
  const before = await (await ctx.request.get('http://localhost:8000/__outbox')).json(); const n0 = before.length; check('claim draft creation sends no email', true);
  await page.click('text=Send to vendor'); await page.waitForSelector('text=Day-7 follow-up: scheduled');
  const after = await (await ctx.request.get('http://localhost:8000/__outbox')).json(); check('claim emailed exactly once to vendor, after the click', after.length === n0 + 1 && after[n0].to === 'ar@sysco.test' && after[n0].pdf_bytes > 500);
  await page.fill('[aria-label="Amount received"]', '20'); await page.click('text=Record'); await page.waitForSelector('text=Recovered $20.00');
  check('credit memo logged; fee = 25% of recovered ($5.00)', (await page.locator('li:has-text("Recovered")').innerText()).includes('$5.00'));

  // 7. dashboard
  await page.goto('http://localhost:3000/dashboard'); await page.waitForSelector('text=Top 5 vendors'); await page.waitForTimeout(300);
  const dash = await page.locator('main').innerText();
  check('dashboard: recovered $20.00, top vendor Sysco, success 100%', dash.includes('$20.00') && dash.includes('Sysco') && dash.includes('100%'));
  await page.screenshot({ path: `${OUT}/dashboard-light.png` });
  await page.goto('http://localhost:3000/review'); await page.waitForSelector('text=Queue is clear'); check('review queue empty state', true);

  // 8. dark mode, RTL/fa, mobile
  const p2 = await browser.newPage({ colorScheme: 'dark', viewport: { width: 1280, height: 800 } });
  await p2.addInitScript((t) => { localStorage.setItem('ab_dev_jwt', t); localStorage.setItem('ab_lang', 'fa'); }, await page.evaluate(() => localStorage.getItem('ab_dev_jwt')));
  await p2.goto('http://localhost:3000/dashboard'); await p2.waitForSelector('text=داشبورد');
  check('RTL applied for fa', (await p2.evaluate(() => document.documentElement.dir)) === 'rtl'); await p2.screenshot({ path: `${OUT}/dashboard-dark-rtl.png` });
  const bg = await p2.evaluate(() => getComputedStyle(document.body).backgroundColor); check('dark mode background is dark', bg !== 'rgb(248, 250, 252)', bg);
  const mob = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2 }); await mob.addInitScript((t) => localStorage.setItem('ab_dev_jwt', t), await page.evaluate(() => localStorage.getItem('ab_dev_jwt')));
  const mp = await mob.newPage(); await mp.goto('http://localhost:3000/invoices'); await mp.waitForSelector('text=E2E-1001');
  check('mobile: no horizontal page overflow', await mp.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)); await mp.screenshot({ path: `${OUT}/invoices-mobile.png` });

  // 9. tenant isolation through the UI: second org sees nothing, cannot open first org's invoice
  const invUrl = (await ctx.request.get('http://localhost:8000/invoices', { headers: { Authorization: `Bearer ${await page.evaluate(() => localStorage.getItem('ab_dev_jwt')) }` } })).json();
  const invId = (await invUrl)[0].id;
  const ctx2 = await mk(uuid()); const q = await ctx2.newPage(); await q.goto('http://localhost:3000/dashboard'); await q.waitForURL('**/onboarding');
  await q.fill('input', 'Rival Group'); await q.click('text=Create workspace'); await q.waitForURL('**/settings');
  await q.goto('http://localhost:3000/invoices'); await q.waitForSelector('text=Nothing here yet'); check('org B sees no invoices from org A', true);
  await q.goto(`http://localhost:3000/invoices/${invId}`); await q.waitForSelector('text=invoice not found'); check("org B opening org A's invoice URL gets an error, not data", !(await q.locator('body').innerText()).includes('E2E-1001'));

  check('no browser console/page errors', errors.length === 0, errors.slice(0, 3).join(' || '));
  await browser.close();
  const failed = results.filter((r) => !r[1]); console.log(`\n${results.length - failed.length}/${results.length} checks passed`); process.exit(failed.length ? 1 : 0);
})().catch((e) => { console.error('E2E CRASH', e); process.exit(2); });
