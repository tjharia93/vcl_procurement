/* VCL_EXISTING_STOCK_PAGE_VERSION: 1. No credentials; every rule and write is checked on the server. */
(() => {
  'use strict';
  const P = 'vcl_procurement.existing_stock.';
  const $ = id => document.getElementById('es-' + id);
  const esc = v => String(v ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const uuid = () => crypto.randomUUID();
  const today = () => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:'Africa/Nairobi',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  };
  const dmy = iso => /^\d{4}-\d{2}-\d{2}/.test(String(iso||'')) ? String(iso).slice(8,10)+'/'+String(iso).slice(5,7)+'/'+String(iso).slice(0,4) : '';
  const num = v => Number(v).toLocaleString('en-GB',{maximumFractionDigits:3});
  const STAGES = [['Tag and sign','s-tag'],['Link items','s-link'],['Ready to draft','s-ready'],['In draft','s-draft'],['Done','s-done']];
  const STAGE_CLASS = Object.fromEntries(STAGES);
  const BOOKS = {'Already recorded':'In books','Opening review needed':'Missing from books'};

  let user='', work=null, filter={company:'',store:'',stage:'',find:''}, sheet=null, busy=false;
  let ticks={}, notes={}, linkSel=new Set(), items=[], chosen=null, searchTimer=0;

  const error = m => { $('error').textContent = m || ''; $('error').hidden = !m; if (m) $('error').scrollIntoView({block:'nearest'}); };
  const status = m => { $('status').textContent = m || ''; };
  const ok = m => { $('ok').textContent = m || ''; $('ok').hidden = !m; };
  const store = {
    get(k){ try { return JSON.parse(localStorage.getItem(`vcl-es-v1:${user}:${k}`) || 'null'); } catch(_) { return null; } },
    set(k,v){ try { localStorage.setItem(`vcl-es-v1:${user}:${k}`, JSON.stringify(v)); } catch(_) {} },
    del(k){ try { localStorage.removeItem(`vcl-es-v1:${user}:${k}`); } catch(_) {} }
  };

  function apiError(j, fallback) {
    try { if (j._server_messages) return JSON.parse(j._server_messages).map(m=>JSON.parse(m).message).join('\n').replace(/<br\s*\/?>/gi,'\n').replace(/<[^>]*>/g,''); } catch (_) {}
    if (j.exc_type === 'PermissionError') return 'You do not have access to this. Existing stock needs the Stock Manager role.';
    return fallback;
  }
  async function api(method, args={}, post=false) {
    let req = {credentials:'same-origin', headers:{Accept:'application/json'}}, url = '/api/method/' + P + method;
    const flat = Object.fromEntries(Object.entries(args).map(([k,v]) => [k, typeof v === 'object' && v !== null ? JSON.stringify(v) : v]));
    if (post) {
      const token = window.frappe?.csrf_token || window.csrf_token;
      if (!token || String(token).includes('{{')) throw Error('Reload this page and sign in to ERPNext before saving.');
      req = {...req, method:'POST', headers:{...req.headers,'Content-Type':'application/json','X-Frappe-CSRF-Token':token}, body:JSON.stringify(args)};
    } else url += '?' + new URLSearchParams(flat);
    const res = await fetch(url, req);
    const json = await res.json().catch(() => ({}));
    if (!res.ok) throw Error(apiError(json, 'The server did not accept this. Check your access and try again.'));
    return json.message;
  }
  function run(fn) {
    return async (...a) => {
      if (busy) return; busy = true; error(''); setBusy();
      try { await fn(...a); }
      catch (e) {
        error(e.message);
        if (/changed\. Reload/i.test(e.message) && sheet) { try { await openSheet(sheet.reference, true); error(e.message + '\nThe sheet has been reloaded with the latest values. Check and try again.'); } catch (_) {} }
      } finally { busy = false; setBusy(); }
    };
  }
  function setBusy() { document.querySelectorAll('#existing-stock button').forEach(b => { if (b.dataset.lock !== 'off') b.toggleAttribute('aria-busy', busy); }); if (sheet) controls(); }

  /* ---------------- worklist ---------------- */
  async function loadWork() {
    status('Loading tagging sheets…');
    work = await api('worklist', filter.company ? {company:filter.company} : {});
    renderWork();
  }
  function renderWork() {
    const companies = work.companies || [];
    $('company').innerHTML = (companies.length > 1 ? '<option value="">All companies</option>' : '') + companies.map(c=>`<option ${c===filter.company?'selected':''}>${esc(c)}</option>`).join('');
    const stores = [...new Set(work.sheets.flatMap(s => s.warehouses))].sort();
    $('store').innerHTML = '<option value="">All stores</option>' + stores.map(w=>`<option ${w===filter.store?'selected':''}>${esc(w)}</option>`).join('');
    const counts = Object.fromEntries(STAGES.map(([n]) => [n, work.sheets.filter(s=>s.stage===n).length]));
    $('stages').innerHTML = [['', `All ${work.sheets.length}`], ...STAGES.map(([n]) => [n, `${n} ${counts[n]}`])]
      .map(([v,t]) => `<button type="button" class="chip" data-stage="${esc(v)}" aria-pressed="${filter.stage===v}">${esc(t)}</button>`).join('');
    const q = filter.find.trim().toLowerCase();
    const list = work.sheets.filter(s => (!filter.store || s.warehouses.includes(filter.store)) && (!filter.stage || s.stage === filter.stage)
      && (!q || s.reference.toLowerCase().includes(q) || s.warehouses.some(w=>w.toLowerCase().includes(q))));
    $('count').textContent = list.length ? `${list.length} sheet${list.length>1?'s':''}. Open one to work through it.` : 'No sheets match. Change the filters, or register existing stock first.';
    $('sheets').innerHTML = list.map(s => {
      const w = n => (n / Math.max(1, s.units) * 100).toFixed(1) + '%';
      const dates = s.first_on === s.last_on ? dmy(s.last_on) : `${dmy(s.first_on)} – ${dmy(s.last_on)}`;
      return `<button type="button" class="sheet" data-ref="${esc(s.reference)}">
        <span><b>${esc(s.warehouses.join(', ') || 'No store')}</b> · ${esc(s.kinds.join(', '))}<br><small class="mono">${esc(s.reference)}</small></span>
        <span class="hide-sm"><small>${s.units} unit${s.units === 1 ? '' : 's'} · ${s.present} present · ${s.not_found} not found · ${s.held} held · ${s.unchecked} to tick</small>
          <span class="meter" aria-hidden="true"><i style="width:${w(s.present)};background:var(--ok)"></i><i style="width:${w(s.held)};background:var(--warn)"></i><i style="width:${w(s.not_found)};background:var(--bad)"></i></span></span>
        <span class="hide-sm muted" style="font-size:13px">Registered ${esc(dates)}</span>
        <span class="pill ${STAGE_CLASS[s.stage]||'s-tag'}">${esc(s.stage)}</span></button>`;
    }).join('');
    status(work.can_set_item ? 'You can link items and prepare drafts.' : 'You can record signed sheets. Only Tanuj links items and prepares drafts.');
  }

  /* ---------------- one sheet ---------------- */
  async function openSheet(ref, keepMsg) {
    status('Loading sheet…');
    sheet = await api('sheet_page', {reference: ref});
    if (!keepMsg) ok('');
    ticks = {}; notes = {};
    const draft = store.get('ticks:' + ref);
    if (draft && draft.ticks) { ticks = draft.ticks; notes = draft.notes || {}; }
    linkSel = new Set(sheet.can_set_item ? sheet.units.filter(u => u.count_outcome === 'Present' && !u.item_code && !activeDraft(u)).map(u => u.name) : []);
    chosen = null; items = []; $('item-q').value = '';
    if (location.hash.slice(1) !== encodeURIComponent(ref)) history.replaceState(null, '', '#' + encodeURIComponent(ref));
    $('list').hidden = true; $('sheet').hidden = false;
    renderSheet();
    status('');
    if (sheet.can_set_item && linkSel.size) searchItems();
  }
  const activeDraft = u => u.stock_draft && ['Draft','Submitted'].includes(u.stock_draft_status);
  const unchecked = () => sheet.units.filter(u => !u.count_outcome);

  function renderSheet() {
    const s = sheet.summary, u0 = sheet.units[0] || {};
    const stores = [...new Set(sheet.units.map(u => u.warehouse).filter(Boolean))];
    $('title').textContent = `${stores.join(', ') || 'Tagging sheet'} · ${sheet.units.length} unit${sheet.units.length === 1 ? '' : 's'}`;
    $('sub').innerHTML = `<span class="mono">${esc(sheet.reference)}</span> · ${esc(sheet.company || u0.company || '')} · <span class="pill ${STAGE_CLASS[s.stage]}">${esc(s.stage)}</span>`;
    const order = ['Tag and sign','Link items','Ready to draft'];
    const at = order.indexOf(s.stage === 'In draft' || s.stage === 'Done' ? 'Done' : s.stage);
    $('steps').innerHTML = [
      ['1 · Tag and sign', `${s.units - s.unchecked} of ${s.units} recorded`],
      ['2 · Link items', `${s.linked} of ${s.present} present units linked`],
      ['3 · Drafts', `${s.in_draft} in draft · ${s.submitted} submitted`]
    ].map(([t, d], i) => `<div class="step ${at === -1 || i < at ? 'done' : i === at ? 'now' : ''}"><b>${t}</b><br><small>${esc(d)}</small></div>`).join('');
    renderRecord(); renderLink(); renderDrafts(); controls();
  }

  /* step 1: record the signed sheet */
  function renderRecord() {
    const todo = unchecked();
    $('record').hidden = !todo.length;
    const done = sheet.units.length - todo.length;
    $('recorded').hidden = !done;
    $('recorded').textContent = done ? `${done} unit${done>1?'s':''} already recorded` + (sheet.returns.length ? ` from ${sheet.returns.length} signed sheet${sheet.returns.length>1?'s':''} (latest signed by ${sheet.returns[0].signed_by} on ${dmy(sheet.returns[0].signed_on)})` : '') + '. Recorded ticks are not changed here.' : '';
    $('rows').innerHTML = todo.map(u => {
      const o = ticks[u.name] || '';
      const seg = [['Present','o-present'],['Not found','o-notfound'],['Held','o-held']]
        .map(([v,c]) => `<button type="button" class="${c}" data-tick="${esc(u.name)}" data-v="${v}" aria-pressed="${o===v}">${v}</button>`).join('');
      return `<tr><td class="mono">${esc(u.name)}</td><td>${esc(u.material)}${u.colour ? ' · ' + esc(u.colour) : ''}<small>${esc(u.spec)}</small></td><td>${esc(u.warehouse)}</td>
        <td><span class="seg" role="group" aria-label="${esc(u.name)} outcome">${seg}</span></td>
        <td><input data-note="${esc(u.name)}" maxlength="350" value="${esc(notes[u.name] || '')}" placeholder="${o && o!=='Present' ? 'Required: why' : 'Optional'}" aria-label="${esc(u.name)} note"></td></tr>`;
    }).join('');
    if (!$('signedon').value) $('signedon').value = today();
    $('signedon').max = today();
  }
  function sheetReady() {
    const todo = unchecked();
    const ticked = todo.filter(u => ticks[u.name]);
    const missingNote = ticked.filter(u => ticks[u.name] !== 'Present' && !(notes[u.name] || '').trim());
    $('ticked').textContent = `${ticked.length} of ${todo.length} ticked` + (missingNote.length ? ` · ${missingNote.length} need a note` : '');
    return ticked.length > 0 && !missingNote.length && $('signedby').value.trim() && $('signedon').value;
  }
  async function saveSheet() {
    const todo = unchecked().filter(u => ticks[u.name]);
    if (!sheetReady()) throw Error('Tick each row you are recording, add a note for every Not found or Held, and enter who signed and when.');
    const key = 'sheetid:' + sheet.reference;
    const rows = todo.map(u => ({name:u.name, modified:u.modified, outcome:ticks[u.name], note:(notes[u.name] || '').trim()}));
    const signature = JSON.stringify([rows.map(r=>[r.name,r.outcome,r.note]), $('signedby').value.trim(), $('signedon').value]);
    let saved = store.get(key);
    if (!saved || saved.signature !== signature) { saved = {id: 'SHEET-' + uuid(), signature}; store.set(key, saved); }
    status('Recording the signed sheet…');
    const res = await api('record_sheet', {sheet_id: saved.id, reference: sheet.reference, signed_by: $('signedby').value.trim(), signed_on: $('signedon').value, rows}, true);
    if (!res || res.stock_posted !== false) throw Error('Unexpected reply. Reload the sheet before trying again.');
    store.del(key); store.del('ticks:' + sheet.reference);
    const counts = rows.reduce((a, r) => (a[r.outcome] = (a[r.outcome] || 0) + 1, a), {});
    await openSheet(sheet.reference, true);
    ok(`Signed sheet recorded${res.reused ? ' (it was already saved)' : ''}: ` + Object.entries(counts).map(([k,v]) => `${v} ${k.toLowerCase()}`).join(', ') + '. Next: link items.');
  }

  /* step 2: link items */
  function renderLink() {
    const present = sheet.units.filter(u => u.count_outcome === 'Present');
    const can = sheet.can_set_item;
    $('p2').classList.toggle('locked', !can);
    $('p2-help').textContent = !present.length ? 'Units appear here once the signed sheet records them as present.'
      : can ? 'Select units, find the ERPNext item that holds their balance, and say whether the books already hold them. Units already on a draft cannot be changed.'
      : 'Only Tanuj links items. You can see the current links here.';
    $('link-rows').innerHTML = present.map(u => {
      const locked = !can || activeDraft(u);
      return `<tr><td><input type="checkbox" data-link="${esc(u.name)}" ${linkSel.has(u.name)?'checked':''} ${locked?'disabled':''} aria-label="Select ${esc(u.name)}"></td>
        <td class="mono">${esc(u.name)}</td><td>${esc(u.material)}<small>${esc(u.spec)}</small></td>
        <td>${u.item_code ? `<b>${esc(u.item_code)}</b><small>${esc(u.stock_uom || '')}</small>` : '<span class="pill s-link">No item</span>'}</td>
        <td>${esc(BOOKS[u.ledger_status] || '—')}${activeDraft(u) ? `<small>On ${esc(u.stock_draft)} (${esc(u.stock_draft_status)})</small>` : ''}</td></tr>`;
    }).join('') || '<tr><td colspan="5" class="muted">No units recorded as present yet.</td></tr>';
    $('link-form').hidden = !can || !present.length;
    $('link-all').disabled = !can;
    renderItems();
  }
  function selectedKinds() { return [...new Set(sheet.units.filter(u => linkSel.has(u.name)).map(u => u.kind))]; }
  async function searchItems() {
    const kinds = selectedKinds();
    if (!kinds.length) { items = []; chosen = null; renderItems(); return; }
    const q = $('item-q').value.trim();
    const res = await api('item_search', {kinds, query: q});
    items = res.items || [];
    if (chosen && !items.some(i => i.name === chosen.name)) chosen = null;
    renderItems();
  }
  function renderItems() {
    const here = new Set(sheet.units.filter(u => linkSel.has(u.name)).map(u => u.warehouse));
    $('items').innerHTML = !linkSel.size ? '<p class="muted">Select one or more units above.</p>'
      : !items.length ? '<p class="muted">No matching stock items with a suitable unit. Try another search, or create the item in ERPNext first.</p>'
      : items.map(i => {
        const bal = (i.balances || []).map(b => `${esc(b.warehouse)} ${num(b.qty)}${here.has(b.warehouse) ? ' (where these units are)' : ''}`).join(' · ');
        return `<button type="button" class="item" data-item="${esc(i.name)}" aria-pressed="${chosen?.name === i.name}"><b>${esc(i.name)}</b> · ${esc(i.item_name)} · ${esc(i.stock_uom)}${i.has_batch_no ? ' · batch tracked' : ''}
          <small>${bal ? 'Books hold: ' + bal : 'No balance in the books'}</small></button>`;
      }).join('');
    controls();
  }
  async function link() {
    const treat = document.querySelector('input[name=es-treat]:checked')?.value;
    if (!chosen || !treat || !linkSel.size) throw Error('Select units, pick an item and say whether the books already hold them.');
    const units = sheet.units.filter(u => linkSel.has(u.name)).map(u => ({name:u.name, modified:u.modified}));
    status(`Linking ${units.length} unit${units.length>1?'s':''} to ${chosen.name}…`);
    const res = await api('set_item', {units, item_code: chosen.name, treatment: treat}, true);
    if (!res || res.stock_posted !== false) throw Error('Unexpected reply. Reload the sheet before trying again.');
    const n = units.length, item = chosen.name;
    await openSheet(sheet.reference, true);
    ok(`${n} unit${n>1?'s':''} linked to ${item} (${treat.toLowerCase()}). No stock was posted.`);
  }

  /* step 3: drafts */
  function renderDrafts() {
    const can = sheet.can_set_item;
    $('p3').classList.toggle('locked', !can);
    const eligible = sheet.units.filter(u => u.count_outcome === 'Present' && u.item_code && !activeDraft(u) && u.status === 'Available');
    const drafted = sheet.units.filter(u => u.stock_draft);
    const groups = [['Opening','Opening review needed','Missing from books → draft opening stock (Stock Reconciliation)'],
                    ['Transfer','Already recorded','In books elsewhere → draft transfer to where the units are (Stock Entry)']];
    const company = sheet.company;
    const whs = (work?.warehouses || []).filter(w => !company || w.company === company);
    let html = '';
    if (!can) html += '<p class="muted">Only Tanuj prepares drafts. You can see existing drafts below.</p>';
    for (const [kind, ledger, title] of groups) {
      const units = eligible.filter(u => u.ledger_status === ledger);
      if (!units.length || !can) continue;
      const itemsIn = [...new Set(units.map(u => u.item_code))];
      html += `<div class="panel" style="margin:10px 0" data-kind="${kind}"><h3>${esc(title)}</h3>
        <p class="muted" style="margin:4px 0 8px">${units.length} unit${units.length>1?'s':''}: ${esc(units.map(u=>u.name).join(', '))}</p>
        <div class="grid">
          <label>Posting date<input type="date" data-f="date" max="${today()}" value="${today()}"></label>
          ${kind === 'Opening' ? itemsIn.map(i => `<label>Approved valuation rate for ${esc(i)} (KES per stock unit)<input data-rate="${esc(i)}" inputmode="decimal" placeholder="e.g. 104.95"></label>`).join('')
            : `<label>Warehouse where the books hold it now<select data-f="source"><option value="">Choose</option>${whs.map(w=>`<option>${esc(w.name)}</option>`).join('')}</select><small>Units already booked where they physically are need no transfer.</small></label>`}
        </div>
        <div class="bar"><button type="button" class="secondary" data-preview="${kind}">Preview draft</button><button type="button" data-prepare="${kind}" disabled>Create draft in ERPNext</button></div>
        <div data-out="${kind}"></div></div>`;
    }
    if (can && !eligible.length) html += '<p class="muted">No units are ready for a draft. Units need to be recorded present, linked to an item, and not already on a draft.</p>';
    if (drafted.length) {
      const by = {};
      drafted.forEach(u => { const k = u.stock_draft_doctype + '|' + u.stock_draft; (by[k] = by[k] || {dt:u.stock_draft_doctype, name:u.stock_draft, status:u.stock_draft_status, units:[]}).units.push(u.name); });
      html += '<h3 style="margin-top:12px">Drafts for this sheet</h3><div class="table-scroll" style="margin-top:6px"><table><thead><tr><th>Voucher</th><th>Status</th><th>Units</th></tr></thead><tbody>' +
        Object.values(by).map(d => `<tr><td><a href="/app/${esc(d.dt.toLowerCase().replace(/ /g,'-'))}/${encodeURIComponent(d.name)}" target="_blank" rel="noopener">${esc(d.dt)} ${esc(d.name)}</a></td><td>${esc(d.status || '—')}</td><td>${esc(d.units.length)}</td></tr>`).join('') +
        '</tbody></table></div>';
    }
    $('drafts').innerHTML = html;
  }
  function draftArgs(kind) {
    const box = document.querySelector(`#es-drafts [data-kind="${kind}"]`);
    const ledger = kind === 'Opening' ? 'Opening review needed' : 'Already recorded';
    const units = sheet.units.filter(u => u.count_outcome === 'Present' && u.item_code && !activeDraft(u) && u.status === 'Available' && u.ledger_status === ledger)
      .map(u => ({name:u.name, modified:u.modified}));
    const args = {kind, units, posting_date: box.querySelector('[data-f=date]').value};
    if (kind === 'Opening') {
      args.rates = {};
      box.querySelectorAll('[data-rate]').forEach(i => {
        const text = i.value.trim().replace(/^kes\s*/i, '');
        if (!/^(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?: \d{3})+(?:\.\d+)?)$/.test(text) || Number(text.replace(/[, ]/g, '')) <= 0)
          throw Error(`Enter the approved valuation rate for ${i.dataset.rate} with a decimal point, e.g. 104.95 or 1,250.50.`);
        args.rates[i.dataset.rate] = Number(text.replace(/[, ]/g, ''));
      });
    } else {
      args.source_warehouse = box.querySelector('[data-f=source]').value;
      if (!args.source_warehouse) throw Error('Choose the warehouse where the books hold this stock now.');
    }
    if (!args.posting_date) throw Error('Choose the posting date.');
    return args;
  }
  async function preview(kind) {
    const args = draftArgs(kind), out = document.querySelector(`#es-drafts [data-out="${kind}"]`);
    status('Checking the draft…');
    const p = await api('draft_preview', args);
    const lines = (p.lines || []).map(l => `<tr><td>${esc(l.item_code)}</td><td>${esc(l.warehouse)}</td><td style="text-align:right">${num(l.qty)} ${esc(l.stock_uom)}</td><td>${l.batch_no ? esc(l.batch_no) : esc(l.tags.length + ' units')}</td>${kind==='Opening'?`<td style="text-align:right">${num(l.valuation_rate)}</td>`:''}</tr>`).join('');
    out.innerHTML = (p.blockers?.length ? `<ul class="blockers">${p.blockers.map(b => `<li>${esc(b)}</li>`).join('')}</ul>` : '<p class="ok-msg" style="margin-top:10px">Nothing blocks this draft.</p>') +
      (lines ? `<div class="table-scroll"><table><thead><tr><th>Item</th><th>Warehouse</th><th>Qty</th><th>Batch / units</th>${kind==='Opening'?'<th>Rate</th>':''}</tr></thead><tbody>${lines}</tbody></table></div>` : '');
    const btn = document.querySelector(`#es-drafts [data-prepare="${kind}"]`);
    btn.disabled = !!p.blockers?.length; btn.dataset.sig = JSON.stringify(args);
    status(p.blockers?.length ? 'Fix what is listed, then preview again.' : 'Preview ready. Create the draft when it looks right.');
  }
  async function prepare(kind) {
    const args = draftArgs(kind), btn = document.querySelector(`#es-drafts [data-prepare="${kind}"]`);
    if (btn.dataset.sig !== JSON.stringify(args)) throw Error('The values changed since the preview. Preview again before creating the draft.');
    const key = `draftid:${sheet.reference}:${kind}`;
    let saved = store.get(key);
    if (!saved || saved.sig !== btn.dataset.sig) { saved = {id: 'EXDRAFT-' + uuid(), sig: btn.dataset.sig}; store.set(key, saved); }
    status('Creating the draft in ERPNext…');
    const res = await api('prepare_draft', {request_id: saved.id, ...args}, true);
    if (!res || res.stock_posted !== false || !res.voucher_name) throw Error('Unexpected reply. Reload the sheet; do not create the draft again until you have checked ERPNext.');
    store.del(key);
    await openSheet(sheet.reference, true);
    ok(`Draft ${res.voucher_doctype} ${res.voucher_name} created${res.reused ? ' (it already existed)' : ''}. Review and submit it in ERPNext; nothing has been posted yet.`);
  }

  function controls() {
    if (!sheet) return;
    $('save-sheet').disabled = busy || !sheetReady();
    const treat = document.querySelector('input[name=es-treat]:checked');
    $('link').disabled = busy || !chosen || !treat || !linkSel.size;
    $('link-sum').textContent = linkSel.size ? `${linkSel.size} selected` + (chosen ? ` → ${chosen.name}` : '') : '';
    const present = sheet.units.filter(u => u.count_outcome === 'Present' && !activeDraft(u));
    $('link-all').checked = present.length > 0 && present.every(u => linkSel.has(u.name));
  }
  function saveTicks() { store.set('ticks:' + sheet.reference, {ticks, notes}); }

  function showList() { sheet = null; $('sheet').hidden = true; $('list').hidden = false; ok(''); if (location.hash) history.replaceState(null, '', location.pathname); }

  async function start() {
    if (!$('status')) return;
    try {
      user = await fetch('/api/method/frappe.auth.get_logged_user', {credentials:'same-origin', headers:{Accept:'application/json'}}).then(r => r.json()).then(j => j.message).catch(() => '');
      if (!user || user === 'Guest') { $('login').hidden = false; throw Error('Sign in with Stock Manager access to work on existing stock.'); }
      await loadWork();
      if (!filter.company && work.companies.length === 1) filter.company = work.companies[0];
      const ref = decodeURIComponent(location.hash.slice(1) || '');
      if (ref) await openSheet(ref); else $('list').hidden = false;
    } catch (e) { error(e.message); status(''); return; }

    $('company').onchange = run(async () => { filter.company = $('company').value; filter.store = ''; await loadWork(); });
    $('store').onchange = () => { filter.store = $('store').value; renderWork(); };
    $('find').oninput = () => { filter.find = $('find').value; renderWork(); };
    $('stages').onclick = e => { const b = e.target.closest('[data-stage]'); if (b) { filter.stage = b.dataset.stage; renderWork(); } };
    $('sheets').onclick = run(async e => { const b = e.target.closest('[data-ref]'); if (b) await openSheet(b.dataset.ref); });
    $('back').onclick = run(async () => { showList(); await loadWork(); });
    $('reload').onclick = run(async () => { await openSheet(sheet.reference); status('Reloaded.'); });
    window.addEventListener('hashchange', run(async () => { const r = decodeURIComponent(location.hash.slice(1)); if (r && r !== sheet?.reference) await openSheet(r); else if (!r && sheet) { showList(); await loadWork(); } }));

    $('print').onclick = run(async () => {
      status('Preparing the tagging sheet…');
      const res = await api('tagging_sheet', {reference: sheet.reference});
      $('frame').srcdoc = res.html; $('preview').hidden = false; $('close').focus(); status('');
    });
    $('close').onclick = () => { $('preview').hidden = true; $('frame').srcdoc = ''; $('print').focus(); };
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && !$('preview').hidden) $('close').click(); });

    $('rows').onclick = e => {
      const b = e.target.closest('[data-tick]'); if (!b || busy) return;
      ticks[b.dataset.tick] = ticks[b.dataset.tick] === b.dataset.v ? '' : b.dataset.v;
      if (!ticks[b.dataset.tick]) delete ticks[b.dataset.tick];
      saveTicks(); renderRecord(); controls();
    };
    $('rows').oninput = e => { const n = e.target.dataset.note; if (n !== undefined) { notes[n] = e.target.value; saveTicks(); controls(); } };
    $('allpresent').onclick = () => { unchecked().forEach(u => { if (!ticks[u.name]) ticks[u.name] = 'Present'; }); saveTicks(); renderRecord(); controls(); };
    $('signedby').oninput = controls; $('signedon').oninput = controls;
    $('save-sheet').onclick = run(saveSheet);

    $('link-rows').onchange = run(async e => { const n = e.target.dataset.link; if (n === undefined) return; e.target.checked ? linkSel.add(n) : linkSel.delete(n); await searchItems(); });
    $('link-all').onchange = run(async () => {
      const present = sheet.units.filter(u => u.count_outcome === 'Present' && !activeDraft(u));
      linkSel = $('link-all').checked ? new Set(present.map(u => u.name)) : new Set();
      renderLink(); await searchItems();
    });
    $('item-q').oninput = () => { clearTimeout(searchTimer); searchTimer = setTimeout(run(searchItems), 300); };
    $('items').onclick = e => { const b = e.target.closest('[data-item]'); if (b) { chosen = items.find(i => i.name === b.dataset.item) || null; renderItems(); } };
    document.querySelectorAll('input[name=es-treat]').forEach(r => r.onchange = controls);
    $('link').onclick = run(link);

    $('drafts').onclick = e => {
      const p = e.target.closest('[data-preview]'), c = e.target.closest('[data-prepare]');
      if (p) run(() => preview(p.dataset.preview))();
      if (c && !c.disabled) run(() => prepare(c.dataset.prepare))();
    };
    $('drafts').oninput = e => { const box = e.target.closest('[data-kind]'); if (box) { const b = box.querySelector('[data-prepare]'); b.disabled = true; } };
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, {once:true}); else start();
})();
