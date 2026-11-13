/* VCL_REEL_BULK_PAGE_VERSION: 1. No credentials or privileged client-side writes. */
(() => {
  'use strict';
  const P = 'vcl_procurement.tagged_stock.';
  const $ = id => document.getElementById('rb-' + id);
  const uuid = () => crypto.randomUUID();
  const today = () => {
    const p = Object.fromEntries(new Intl.DateTimeFormat('en-CA', {timeZone:'Africa/Nairobi', year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(x=>[x.type,x.value]));
    return `${p.year}-${p.month}-${p.day}`;
  };
  const fresh = () => ({version:1,batch:uuid(),shared:{company:'',warehouse:'',observed_on:today(),material:'',gsm:'',brightness:'',width_mm:'',colour:'',notes:''},rows:[],locked:false});
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const error = message => { $('error').textContent = message || ''; $('error').hidden = !message; };
  const status = message => { $('status').textContent = message; };
  let state, options, storageKey, busy=false, review=false, labelUrl='';

  function numeric(raw, label, optional=false) {
    let text=String(raw ?? '').trim();
    if(optional && (!text || /^(\?|unknown)$/i.test(text))) return null;
    text=text.replace(/\s*kg$/i,'').trim();
    if(!/^(?:\d+(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{1,3}(?: \d{3})+(?:\.\d+)?)$/.test(text)) throw Error(`${label}: enter a positive number with a decimal point, e.g. 261.5.`);
    const value=Number(text.replace(/[, ]/g,''));
    if(!Number.isFinite(value) || value<=0 || value>1000000) throw Error(`${label}: enter a positive number up to 1,000,000.`);
    return value;
  }
  function persist() {
    try { localStorage.setItem(storageKey,JSON.stringify(state)); }
    catch (_) { throw Error('This browser could not save the batch recovery references. Enable site storage before registering reels.'); }
  }
  function checkedBatch(value) {
    if(!value || value.version!==1 || typeof value.batch!=='string' || !/^[a-f0-9-]{36}$/i.test(value.batch) || !Array.isArray(value.rows) || value.rows.length>200 || !value.shared || value.rows.some(r=>
      typeof r.reference!=='string' || !r.reference.startsWith(`EXIST-BULK-${value.batch}-`) || !/^EXIST-BULK-[a-f0-9-]+$/i.test(r.reference) || typeof r.weight!=='string' || typeof r.note!=='string' || (value.locked && (!r.plan || r.plan.kind!=='Reel')))) {
      throw Error('The batch record format is not valid. Keep a copy and ask a manager before creating replacement reels.');
    }
    if(new Set(value.rows.map(r=>r.reference)).size!==value.rows.length) throw Error('The batch record contains duplicate row references.');
    if(value.locked) checkFrozenPlans(value);
    return value;
  }
  function apiError(j, fallback) {
    try { if(j._server_messages) return JSON.parse(j._server_messages).map(m=>JSON.parse(m).message).join(' · ').replace(/<[^>]*>/g,''); } catch (_) {}
    return j.exc_type ? `${j.exc_type}: ${fallback}` : fallback;
  }
  async function api(method,args={},post=false) {
    let request={credentials:'same-origin',headers:{Accept:'application/json'}};
    let url='/api/method/'+method;
    if(post) {
      const token=window.frappe?.csrf_token || window.csrf_token;
      if(!token || String(token).includes('{{')) throw Error('Reload this page and sign in to ERPNext before saving.');
      request={...request,method:'POST',headers:{...request.headers,'Content-Type':'application/json','X-Frappe-CSRF-Token':token},body:JSON.stringify(args)};
    } else url+='?'+new URLSearchParams(args);
    const response=await fetch(url,request);
    const json=await response.json().catch(()=>({}));
    if(!response.ok) throw Error(apiError(json,'Request failed. Check your access and try again.'));
    return json.message;
  }
  function addRows(weights) {
    if(state.locked || review || busy) return;
    if(state.rows.length+weights.length>200) throw Error('Use a maximum of 200 reels per batch.');
    state.rows.push(...weights.map(weight=>({reference:`EXIST-BULK-${state.batch}-${uuid()}`,weight:weight===null?'':String(weight),note:'',selected:false,unit:null,error:'',plan:null})));
    persist(); renderRows();
  }
  function planFor(row,index,shared) {
    const s=shared || state.shared;
    if(!options.companies.includes(s.company)) throw Error('Choose a company you can access.');
    if(!options.warehouses.some(w=>w.name===s.warehouse && w.company===s.company)) throw Error('Choose the actual warehouse in this company.');
    if(!/^\d{4}-\d{2}-\d{2}$/.test(s.observed_on) || s.observed_on>today()) throw Error('Choose the actual registration date, no later than today.');
    const gsm=numeric(s.gsm,'GSM'), width=numeric(s.width_mm,'Reel width');
    const brightness=numeric(s.brightness,'Brightness',true);
    let material=s.material.trim();
    if(!material) throw Error('Enter the material name.');
    if(brightness!==null) {
      if(/brightness/i.test(material)) throw Error('Enter brightness once, in its own field, rather than in the material name.');
      material+=` - ${brightness}% BRIGHTNESS`;
    }
    if(material.length>80) throw Error('The material name plus brightness must fit within 80 characters.');
    const notes=[s.notes.trim(),row.note.trim()].filter(Boolean).join('\n');
    if(notes.length>2000) throw Error(`Row ${index+1}: combined notes must fit within 2,000 characters.`);
    return {kind:'Reel',company:s.company,warehouse:s.warehouse,observed_on:s.observed_on,material,gsm,width_mm:width,
      colour:s.colour.trim(),notes,count:1,existing_ids:[],net_kg:numeric(row.weight,`Row ${index+1} net kg`,true)};
  }
  function checkFrozenPlans(batch) {
    const canonical=p=>JSON.stringify(Object.fromEntries(Object.keys(p).sort().map(k=>[k,p[k]])));
    batch.rows.forEach((row,i)=>{
      if(canonical(row.plan)!==canonical(planFor(row,i,batch.shared))) throw Error(`Row ${i+1}: the recovery plan differs from the displayed details. Keep the original batch record and ask a manager to review it.`);
    });
  }
  function totals() {
    let kg=0,known=0;
    for(const r of state.rows) { try { const n=numeric(r.weight,'Weight',true);if(n!==null){kg+=n;known++;} }catch(_){} }
    const saved=state.rows.filter(r=>r.unit).length;
    return `${state.rows.length} reels · ${known} known weights totalling ${kg.toLocaleString(undefined,{maximumFractionDigits:3})} kg · ${state.rows.length-known} unknown / unfinished · ${saved} saved`;
  }
  function controls() {
    $('common').disabled=busy || review || state.locked;
    $('entry').disabled=busy || review || state.locked;
    $('review').hidden=review || state.locked;
    $('review').disabled=busy || !state.rows.length;
    $('edit').hidden=!review || state.locked;
    $('edit').disabled=busy;
    $('review-panel').hidden=!review && !state.locked;
    $('verified').disabled=busy;
    const remaining=state.rows.filter(r=>!r.unit).length;
    $('save').textContent=busy?'Working…':state.locked?`Save / retry ${remaining} unfinished reels`:`Register ${state.rows.length} reviewed reels`;
    $('save').disabled=busy || !$('verified').checked || !remaining;
    $('print').disabled=busy || !state.rows.some(r=>r.unit && r.selected);
    $('select').disabled=busy || !state.rows.some(r=>r.unit);
    $('backup').disabled=busy || !state.rows.length;
    $('new').disabled=busy || (state.locked && remaining>0);
    $('restore').disabled=busy || (state.locked && remaining>0);
    $('totals').textContent=totals();
    $('batch').textContent='Batch reference: '+state.batch;
  }
  function renderRows() {
    $('rows').innerHTML=state.rows.map((r,i)=>`<tr><td><input aria-label="Print row ${i+1}" type="checkbox" data-select="${i}" ${r.selected?'checked':''} ${!r.unit||busy?'disabled':''}></td><td>${i+1}</td><td><input aria-label="Row ${i+1} net kg" data-weight="${i}" inputmode="decimal" value="${esc(r.weight)}" placeholder="Unknown" ${busy||review||state.locked?'disabled':''}></td><td><input aria-label="Row ${i+1} note" data-note="${i}" maxlength="350" value="${esc(r.note)}" ${busy||review||state.locked?'disabled':''}></td><td class="status-cell">${r.unit?`<a class="saved" href="/compass/stock-tags/${encodeURIComponent(r.unit.name)}" target="_blank" rel="noopener">${esc(r.unit.name)}</a><br>Saved · ${r.unit.net_kg==null?'Weight unknown':esc(r.unit.net_kg)+' kg'}`:r.error?esc(r.error):state.locked?'Waiting to save':'Ready for review'}</td><td>${!state.locked&&!review?`<button class="secondary small" data-remove="${i}" ${busy?'disabled':''}>Remove</button>`:''}</td></tr>`).join('');
    controls();
  }
  function warehouseOptions() {
    $('warehouse').innerHTML='<option value="">Choose warehouse</option>'+options.warehouses.filter(w=>w.company===state.shared.company).map(w=>`<option value="${esc(w.name)}">${esc(w.name)}</option>`).join('');
    $('warehouse').value=state.shared.warehouse;
  }
  function renderCommon() {
    $('company').innerHTML='<option value="">Choose company</option>'+options.companies.map(c=>`<option>${esc(c)}</option>`).join('');
    for(const input of document.querySelectorAll('#reel-bulk [data-shared]')) input.value=state.shared[input.dataset.shared] || '';
    warehouseOptions(); $('date').max=today();
  }
  function reviewRows() {
    if(!state.rows.length) throw Error('Add at least one reel.');
    const plans=state.rows.map((row,i)=>planFor(row,i));
    state.rows.forEach((r,i)=>r.plan=plans[i]);
    persist(); review=true; $('verified').checked=false;
    $('summary').textContent=`${plans.length} individual reels · ${plans[0].material} · ${plans[0].gsm} GSM · ${plans[0].width_mm} mm · ${plans[0].warehouse} · ${plans[0].observed_on}\n${totals()}`;
    renderRows(); $('review-panel').scrollIntoView({block:'nearest',behavior:'smooth'});
  }
  async function saveRows() {
    if(busy || !$('verified').checked || (!review && !state.locked)) return;
    if(!state.rows.length || state.rows.some(r=>!r.plan)) throw Error('Review all reel details before saving.');
    checkFrozenPlans(state);
    busy=true; error(''); state.locked=true; persist(); renderRows();
    try {
      for(let i=0;i<state.rows.length;i++) {
        const row=state.rows[i]; if(row.unit) continue;
        row.error='Saving…'; renderRows(); status(`Registering row ${i+1} of ${state.rows.length}…`);
        try {
          persist(); // The reference and exact plan must survive a lost response or browser refresh.
          const result=await api(P+'register',{reference:row.reference,plan:row.plan,physically_verified:1},true);
          const unit=result?.units?.[0];
          if(result?.name!==row.reference || result.units.length!==1 || !unit?.name || unit.registration!==row.reference || result.stock_posted!==false) throw Error('Unexpected registration response. Keep this batch and retry the same row.');
          row.unit=unit; row.selected=true; row.error=''; persist();
        } catch(e) {
          row.error='Needs retry: '+e.message;
          try {persist();}catch(_){}
          error(`Stopped at row ${i+1}. ${e.message}\nCompleted rows remain saved. Retry this same batch; do not create replacement rows for an uncertain save.`);
          break;
        }
      }
    } finally { busy=false; renderRows(); status(totals()); }
  }
  async function printRows() {
    if(busy) return;
    const rows=state.rows.filter(r=>r.unit && r.selected); if(!rows.length) return;
    busy=true; controls(); error('');
    try {
      let documentForPrint;
      for(let i=0;i<rows.length;i++) {
        status(`Preparing label ${i+1} of ${rows.length}…`);
        const result=await api(P+'print_labels',{name:rows[i].unit.name});
        const doc=new DOMParser().parseFromString(result.html,'text/html');
        const pages=doc.querySelectorAll('article.label-sheet');
        if(pages.length!==1 || pages[0].querySelector('h1')?.textContent.trim()!==rows[i].unit.name) throw Error('The saved label did not match the selected reel. Open its record in Compass.');
        if(!documentForPrint) documentForPrint=doc;
        else documentForPrint.body.appendChild(documentForPrint.importNode(pages[0],true));
      }
      documentForPrint.title=`${rows.length} existing reel labels`;
      const tools=documentForPrint.querySelector('.tools');
      tools.innerHTML=`<b>${rows.length} reel labels · 100 × 70 mm</b><br>Actual size / 100%. Headers and footers off.<button onclick="window.print()">Print</button>`;
      const html='<!doctype html>'+documentForPrint.documentElement.outerHTML;
      if(labelUrl) URL.revokeObjectURL(labelUrl);
      labelUrl=URL.createObjectURL(new Blob([html],{type:'text/html;charset=utf-8'}));
      $('download-labels').href=labelUrl; $('frame').srcdoc=html; $('preview').hidden=false;
      $('close').focus(); status(totals());
    } finally {busy=false;controls();}
  }
  function backup() {
    const blob=new Blob([JSON.stringify(state,null,2)],{type:'application/json'}), url=URL.createObjectURL(blob);
    const a=document.createElement('a');a.href=url;a.download=`reel-batch-${state.batch}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  function action(fn) {return async()=>{error('');try{await fn();}catch(e){busy=false;error(e.message);if(state)controls();}};}
  async function start() {
    if(!$('app')) return;
    try {
      const user=await api('frappe.auth.get_logged_user');
      if(!user || user==='Guest') {$('login').hidden=false;throw Error('Sign in with Stock Manager access to register reels.');}
      options=await api(P+'options');
      storageKey='vcl-reel-bulk-v1:'+user;
      const stored=localStorage.getItem(storageKey);
      if(stored) {
        state=checkedBatch(JSON.parse(stored));
      } else state=fresh();
      if(!state.shared.company && options.companies.length===1) state.shared.company=options.companies[0];
      review=state.locked; renderCommon(); renderRows();
      if(review && state.rows.length) $('summary').textContent=`Resuming batch ${state.batch}. Shared details are locked to the original reviewed values.\n${totals()}`;
      $('app').hidden=false; status(stored?'Saved batch restored. '+totals():'Ready. Enter shared details and add one row per physical reel.');
      persist();
      document.querySelectorAll('#reel-bulk [data-shared]').forEach(input=>input.addEventListener('input',action(()=>{
        if(state.locked || review || busy) return;
        state.shared[input.dataset.shared]=input.value;
        if(input.dataset.shared==='company'){state.shared.warehouse='';warehouseOptions();}
        persist();
      })));
      $('rows').addEventListener('input',event=>{
        try {
          const t=event.target;
          if(t.dataset.select!==undefined){state.rows[Number(t.dataset.select)].selected=t.checked;persist();controls();return;}
          if(state.locked || review || busy) return;
          if(t.dataset.weight!==undefined) state.rows[Number(t.dataset.weight)].weight=t.value;
          if(t.dataset.note!==undefined) state.rows[Number(t.dataset.note)].note=t.value;
          persist();controls();
        }catch(e){error(e.message);}
      });
      $('rows').addEventListener('click',event=>{const index=event.target.dataset.remove;if(index!==undefined&&!state.locked&&!review&&!busy){state.rows.splice(Number(index),1);try{persist();renderRows();}catch(e){error(e.message);}}});
      $('add').onclick=action(()=>addRows([null]));
      $('add-paste').onclick=action(()=>{
        const lines=$('paste').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean);
        if(!lines.length) throw Error('Paste one weight per line. Use ? for an unknown weight.');
        const weights=lines.map((s,i)=>numeric(s,`Pasted line ${i+1}`,true));
        addRows(weights);$('paste').value='';
      });
      $('review').onclick=action(reviewRows);
      $('edit').onclick=action(()=>{if(!state.locked){review=false;$('verified').checked=false;renderRows();}});
      $('verified').onchange=controls;
      $('save').onclick=action(saveRows);
      $('print').onclick=action(printRows);
      $('select').onclick=action(()=>{state.rows.forEach(r=>r.selected=!!r.unit);persist();renderRows();});
      $('backup').onclick=action(backup);
      $('restore').onchange=action(async()=>{
        const file=$('restore').files[0];if(!file) return;
        if(busy || (state.locked && state.rows.some(r=>!r.unit))) throw Error('Finish this saved batch before restoring another.');
        if(file.size>2000000) throw Error('Choose a batch record smaller than 2 MB.');
        const restored=checkedBatch(JSON.parse(await file.text()));
        if(state.rows.length && !confirm('Replace the batch shown in this browser with this backup? Existing saved reels stay in Compass.')) return;
        state=restored;review=state.locked;$('verified').checked=false;persist();renderCommon();renderRows();
        $('summary').textContent=`Restored batch ${state.batch}. Review saved rows and retry unfinished rows using their original references.\n${totals()}`;
        status('Batch record restored. No reels have been registered by restoring it.');$('restore').value='';
      });
      $('new').onclick=action(()=>{
        if(state.locked && state.rows.some(r=>!r.unit)) throw Error('Finish or retry this batch before starting another.');
        if(state.rows.length && !confirm('Start a new batch? Download the current batch record first if you need a copy. Saved reels remain in Compass.')) return;
        const company=state.shared.company,warehouse=state.shared.warehouse;
        state=fresh();state.shared.company=company;state.shared.warehouse=warehouse;review=false;$('verified').checked=false;persist();renderCommon();renderRows();status('New batch ready.');
      });
      $('close').onclick=()=>{$('preview').hidden=true;$('frame').srcdoc='';$('print').focus();};
      document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!$('preview').hidden)$('close').click();});
    }catch(e){error(e.message);status('No registration has been started from this page.');}
  }
  if(document.readyState==='loading') document.addEventListener('DOMContentLoaded',start,{once:true}); else start();
})();
