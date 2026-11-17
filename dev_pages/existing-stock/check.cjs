// Offline browser verification. Every request is intercepted; no ERPNext records are created.
// Run: node dev_pages/existing-stock/check.cjs   (PLAYWRIGHT=<path to playwright> if it is not on the module path)
const {chromium} = require(process.env.PLAYWRIGHT || 'playwright');
const fs = require('node:fs/promises');
const assert = require('node:assert/strict');
const path = require('node:path');

const REF_SG = 'EXIST-BULK-11111111-1111-1111-1111-111111111111', REF_MN = 'EXIST-MANILA';
function units() {
  return [
    ...[25, 26, 27].map((n, i) => ({name:`VCL-RL-2026-006${n}`, registration:`${REF_SG}-r${i}`, company:'VCL', kind:'Reel', material:'WHITE SEMI GLOSS', warehouse:'Warehouse 7',
      status:'Available', count_outcome:'', item_code:'', stock_uom:'', ledger_status:'', stock_draft:'', stock_draft_doctype:'', stock_draft_status:'', modified:`2026-11-17 10:00:0${i}`, spec:'1000 mm · 80 gsm · 512 kg', net_kg:512, weight_known:1})),
    {name:'VCL-BND-2026-00001', registration:REF_MN, company:'VCL', kind:'Bundle', material:'MANILA', colour:'Green', warehouse:'Soykimau', status:'Available', count_outcome:'Present',
      item_code:'', stock_uom:'', ledger_status:'', stock_draft:'', stock_draft_doctype:'', stock_draft_status:'', modified:'2026-11-17 10:00:09', spec:'610 × 670 mm · 160 gsm · 8 reams × 100 sheets'}
  ];
}
function summary(us) {
  const s = {units:us.length, present:0, not_found:0, held:0, unchecked:0, need_item:0, linked:0, in_draft:0, submitted:0};
  for (const u of us) {
    if (u.count_outcome === 'Present') s.present++; else if (u.count_outcome === 'Not found') s.not_found++; else if (u.count_outcome === 'Held') s.held++; else s.unchecked++;
    if (u.count_outcome !== 'Present') continue;
    if (!u.item_code) { s.need_item++; continue; }
    s.linked++; if (u.stock_draft_status === 'Draft') s.in_draft++; if (u.stock_draft_status === 'Submitted') s.submitted++;
  }
  s.stage = !us.length || s.unchecked ? 'Tag and sign' : s.need_item ? 'Link items' : s.in_draft ? 'In draft' : s.linked > s.submitted ? 'Ready to draft' : 'Done';
  return s;
}

(async () => {
  const html = await fs.readFile(path.join(__dirname, 'page.html'), 'utf8');
  const js = await fs.readFile(path.join(__dirname, 'page.js'), 'utf8');
  const browser = await chromium.launch({headless:true});
  const shots = process.env.SHOTS;

  async function session({approver = true, width = 1280} = {}) {
    const db = units(), posts = [], sheets = new Map(), drafts = new Map();
    let dropFirstSheet = true;
    const ctx = await browser.newContext({viewport:{width, height:1000}});
    await ctx.route('**/*', async route => {
      const req = route.request(), url = new URL(req.url());
      if (url.pathname === '/existing-stock-dev') return route.fulfill({contentType:'text/html; charset=utf-8', body:'<!doctype html><meta name=viewport content="width=device-width,initial-scale=1"><body>' + html + '<script>window.frappe={csrf_token:"offline-test-only"};</script><script>' + js + '</script>'});
      const method = url.pathname.replace('/api/method/', '');
      const of = ref => db.filter(u => ref === REF_SG ? u.registration.startsWith(REF_SG) : u.registration === ref);
      let message;
      if (method === 'frappe.auth.get_logged_user') message = 'offline@example.test';
      else if (method.endsWith('.worklist')) message = {companies:['VCL'], warehouses:[{name:'Stores', company:'VCL'}, {name:'Warehouse 7', company:'VCL'}], can_set_item:approver,
        sheets:[REF_SG, REF_MN].map(r => ({reference:r, company:'VCL', kinds:[of(r)[0].kind], warehouses:[of(r)[0].warehouse], first_on:'2026-11-13', last_on:'2026-11-14', registrations:of(r).length, ...summary(of(r))}))};
      else if (method.endsWith('.sheet_page')) { const r = url.searchParams.get('reference'); message = {reference:r, company:'VCL', units:of(r), summary:summary(of(r)), can_set_item:approver, returns:[...sheets.values()].filter(s => s.reference === r)}; }
      else if (method.endsWith('.tagging_sheet')) message = {html:'<!doctype html><title>Sheet</title><button onclick="window.print()">Print</button><h1>Tagging sheet</h1>'};
      else if (method.endsWith('.item_search')) {
        const kinds = JSON.parse(url.searchParams.get('kinds'));
        message = {items: kinds.includes('Reel') ? [{name:'SG-80', item_name:'Semi gloss 80 gsm', stock_uom:'Kg', has_batch_no:0, balances:[{warehouse:'Stores', qty:1536}]}] : [{name:'MANILA-160', item_name:'Manila 160', stock_uom:'Ream', has_batch_no:0, balances:[]}]};
      } else if (req.method() === 'POST') {
        assert.equal(req.headers()['x-frappe-csrf-token'], 'offline-test-only');
        const b = req.postDataJSON(); posts.push([method.split('.').pop(), b]);
        if (method.endsWith('.record_sheet')) {
          const reused = sheets.has(b.sheet_id);  // the server answers a repeated sheet ID before it checks versions
          if (!reused) for (const r of b.rows) { const u = db.find(x => x.name === r.name); assert.equal(r.modified, u.modified, 'sends the loaded version'); }
          if (!reused) { sheets.set(b.sheet_id, {name:b.sheet_id, reference:b.reference, signed_by:b.signed_by, signed_on:b.signed_on}); for (const r of b.rows) Object.assign(db.find(x => x.name === r.name), {count_outcome:r.outcome, modified:'2026-11-17 11:00:00'}); }
          if (dropFirstSheet) { dropFirstSheet = false; return route.abort('failed'); }
          message = {name:b.sheet_id, reused, units:[], stock_posted:false};
        } else if (method.endsWith('.set_item')) {
          for (const r of b.units) Object.assign(db.find(x => x.name === r.name), {item_code:b.item_code, stock_uom:'Kg', ledger_status:b.treatment === 'In books' ? 'Already recorded' : 'Opening review needed', modified:'2026-11-17 12:00:00'});
          message = {units:[], stock_posted:false};
        } else if (method.endsWith('.prepare_draft')) {
          const reused = drafts.has(b.request_id);
          if (!reused) { drafts.set(b.request_id, 'MAT-RECO-2026-00001'); for (const r of b.units) Object.assign(db.find(x => x.name === r.name), {stock_draft:'MAT-RECO-2026-00001', stock_draft_doctype:'Stock Reconciliation', stock_draft_status:'Draft'}); }
          message = {name:b.request_id, voucher_doctype:'Stock Reconciliation', voucher_name:drafts.get(b.request_id), reused, stock_posted:false};
        } else throw Error('Unexpected POST ' + method);
      } else if (method.endsWith('.draft_preview')) {
        const rates = JSON.parse(url.searchParams.get('rates') || '{}');
        message = {blockers:[], lines:[{item_code:'SG-80', warehouse:'Warehouse 7', qty:1024, stock_uom:'Kg', batch_no:null, tags:['a','b'], valuation_rate:rates['SG-80']}]};
      } else throw Error('Unexpected request ' + req.method() + ' ' + url.pathname);
      return route.fulfill({contentType:'application/json', body:JSON.stringify({message})});
    });
    const page = await ctx.newPage(), errors = [];
    page.on('pageerror', e => errors.push(e.message));
    await page.goto('https://site.example.test/existing-stock-dev');
    await page.locator('#es-list').waitFor({state:'visible'});
    return {ctx, page, posts, db, errors};
  }

  try {
    // Approver, desktop: the whole path for the semi-gloss sheet.
    const {page, posts, errors, ctx} = await session();
    assert.equal(await page.locator('.sheet').count(), 2);
    assert.equal(posts.length, 0, 'loading writes nothing');
    if (shots) await page.screenshot({path:path.join(shots, 'list.png'), fullPage:true});
    await page.locator('#es-stages [data-stage="Link items"]').click();
    assert.equal(await page.locator('.sheet').count(), 1);
    await page.locator('#es-stages [data-stage=""]').click();
    await page.locator(`.sheet[data-ref="${REF_SG}"]`).click();
    await page.locator('#es-sheet').waitFor({state:'visible'});
    await page.locator('#es-print').click(); await page.locator('#es-preview').waitFor({state:'visible'}); await page.locator('#es-close').click();

    // Step 1: ticks, note rule, lost response then retry with the same sheet ID.
    await page.locator('#es-allpresent').click();
    await page.locator('[data-tick="VCL-RL-2026-00627"][data-v="Held"]').click();
    await page.locator('#es-signedby').fill('Peter Store');
    assert.equal(await page.locator('#es-save-sheet').isEnabled(), false, 'Held needs a note');
    await page.locator('[data-note="VCL-RL-2026-00627"]').fill('Core damaged');
    assert.equal(await page.locator('#es-save-sheet').isEnabled(), true);
    await page.locator('#es-save-sheet').click();
    await page.getByRole('alert').waitFor();
    await page.locator('#es-save-sheet').click();
    await page.locator('#es-ok').filter({hasText:'Signed sheet recorded'}).waitFor();
    const sheetPosts = posts.filter(p => p[0] === 'record_sheet');
    assert.equal(sheetPosts.length, 2); assert.equal(sheetPosts[0][1].sheet_id, sheetPosts[1][1].sheet_id, 'retry reuses the sheet ID');
    assert.deepEqual(sheetPosts[1][1].rows.map(r => r.outcome), ['Present', 'Present', 'Held']);
    assert.equal(await page.locator('#es-record').isHidden(), true);

    // Step 2: present units preselected, item search, treatment, link.
    assert.equal(await page.locator('[data-link]:checked').count(), 2);
    await page.locator('[data-item="SG-80"]').waitFor();
    assert.match(await page.locator('[data-item="SG-80"]').innerText(), /Stores 1,536/);
    await page.locator('[data-item="SG-80"]').click();
    assert.equal(await page.locator('#es-link').isEnabled(), false, 'needs a books answer');
    await page.locator('input[name=es-treat][value="Missing from books"]').check();
    await page.locator('#es-link').click();
    await page.locator('#es-ok').filter({hasText:'linked to SG-80'}).waitFor();
    const link = posts.find(p => p[0] === 'set_item')[1];
    assert.deepEqual(link.units.map(u => u.name), ['VCL-RL-2026-00625', 'VCL-RL-2026-00626']);
    assert.ok(link.units.every(u => u.modified === '2026-11-17 11:00:00'), 'sends the reloaded version');

    // Step 3: comma-decimal refused, preview, create, never twice.
    await page.locator('[data-rate="SG-80"]').fill('104,95');
    await page.locator('[data-preview="Opening"]').click();
    await page.getByRole('alert').filter({hasText:'decimal point'}).waitFor();
    await page.locator('[data-rate="SG-80"]').fill('104.95');
    assert.equal(await page.locator('[data-prepare="Opening"]').isEnabled(), false, 'preview first');
    await page.locator('[data-preview="Opening"]').click();
    await page.locator('[data-prepare="Opening"]:not([disabled])').waitFor();
    if (shots) await page.screenshot({path:path.join(shots, 'sheet.png'), fullPage:true});
    await page.locator('[data-prepare="Opening"]').click();
    await page.locator('#es-ok').filter({hasText:'MAT-RECO-2026-00001'}).waitFor();
    const d = posts.filter(p => p[0] === 'prepare_draft');
    assert.equal(d.length, 1); assert.deepEqual(d[0][1].rates, {'SG-80':104.95});
    assert.equal(await page.locator('[data-prepare]').count(), 0, 'drafted units leave the draft form');
    assert.match(await page.locator('#es-drafts').innerText(), /Stock Reconciliation MAT-RECO-2026-00001/);
    assert.deepEqual(errors, []);
    await ctx.close();

    // Not Tanuj, phone width: can record sheets, cannot link or draft; no sideways scroll.
    const s2 = await session({approver:false, width:390});
    await s2.page.locator(`.sheet[data-ref="${REF_MN}"]`).click();
    await s2.page.locator('#es-sheet').waitFor({state:'visible'});
    assert.equal(await s2.page.locator('#es-link-form').isHidden(), true);
    assert.equal(await s2.page.locator('[data-link]:not([disabled])').count(), 0);
    assert.equal(await s2.page.locator('[data-link]:checked').count(), 0, 'nothing preselected for someone who cannot link');
    assert.equal(await s2.page.locator('[data-prepare]').count(), 0);
    const wide = await s2.page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
    assert.equal(wide, false, 'no sideways page scroll at 390 px');
    if (shots) await s2.page.screenshot({path:path.join(shots, 'phone.png'), fullPage:true});
    assert.equal(s2.posts.length, 0); assert.deepEqual(s2.errors, []);
    await s2.ctx.close();
    console.log('existing-stock page: all offline checks passed');
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exit(1); });
