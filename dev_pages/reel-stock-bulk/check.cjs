// Offline browser verification. Every request is intercepted; no ERPNext records are created.
const {chromium}=require('C:/Users/tanuj/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs/promises');
const assert=require('node:assert/strict');
const path=require('node:path');
(async()=>{
  const html=await fs.readFile(path.join(__dirname,'page.html'),'utf8');
  const js=await fs.readFile(path.join(__dirname,'page.js'),'utf8');
  const records=new Map(), posts=[], prints=[];
  let lostResponse=false;
  const browser=await chromium.launch({headless:true,channel:'msedge'});
  async function context(guest=false){
    const context=await browser.newContext({viewport:{width:1280,height:1000},acceptDownloads:true});
    await context.route('**/*',async route=>{
      const request=route.request(),url=new URL(request.url());
      if(url.pathname==='/') return route.fulfill({contentType:'text/html',body:html+'<script>window.frappe={csrf_token:"offline-test-only"};</script><script>'+js+'</script>'});
      const method=url.pathname.replace('/api/method/','');
      let message;
      if(method==='frappe.auth.get_logged_user') message=guest?'Guest':'offline@example.test';
      else if(method.endsWith('.options')) message={companies:['Test Company'],warehouses:[{name:'Test Warehouse',company:'Test Company'}]};
      else if(method.endsWith('.register')){
        assert.equal(request.method(),'POST');assert.equal(request.headers()['x-frappe-csrf-token'],'offline-test-only');
        const body=request.postDataJSON();posts.push(body);
        assert.equal(body.physically_verified,1);assert.equal(body.plan.count,1);assert.deepEqual(body.plan.existing_ids,[]);
        let unit=records.get(body.reference);
        const reused=!!unit;
        if(!unit){unit={name:`VCL-RL-2099-${String(90001+records.size)}`,registration:body.reference,kind:'Reel',material:body.plan.material,net_kg:body.plan.net_kg,weight_known:body.plan.net_kg!==null};records.set(body.reference,unit);}
        if(records.size===2&&!lostResponse){lostResponse=true;return route.abort('failed');}
        message={name:body.reference,reused,units:[unit],stock_posted:false};
      } else if(method.endsWith('.print_labels')){
        const name=url.searchParams.get('name');prints.push(name);
        const unit=[...records.values()].find(r=>r.name===name);assert.ok(unit);
        message={html:`<!doctype html><html><head><title>Label</title><style>@page{size:100mm 70mm}.label-sheet{width:100mm;height:70mm}</style></head><body><div class="tools"><button onclick="window.print()">Print</button></div><article class="label-sheet"><section class="po-reel-label"><h1>${name}</h1><p>${unit.material}</p><p>${unit.net_kg??'Unknown'} kg</p></section></article></body></html>`};
      } else throw Error('Unexpected network request: '+request.method()+' '+url.pathname);
      return route.fulfill({contentType:'application/json',body:JSON.stringify({message})});
    });
    return context;
  }
  try{
    const c=await context(),page=await c.newPage();
    await page.goto('https://reel-bulk.example.test/');await page.locator('#rb-app').waitFor({state:'visible'});
    assert.equal(posts.length,0);
    await page.locator('#rb-warehouse').selectOption('Test Warehouse');
    await page.locator('#rb-material').fill('WOODFREE');await page.locator('#rb-gsm').fill('44');
    await page.locator('#rb-brightness').fill('66');await page.locator('#rb-width').fill('610');
    await page.locator('#rb-paste').fill('261,5');await page.locator('#rb-add-paste').click();
    await page.getByRole('alert').filter({hasText:'decimal point'}).waitFor();assert.equal(await page.locator('#rb-rows tr').count(),0);
    await page.locator('#rb-paste').fill('261.5\n248.0\n?\n273.5');await page.locator('#rb-add-paste').click();
    assert.equal(await page.locator('#rb-rows tr').count(),4);
    await page.locator('#rb-review').click();assert.equal(await page.locator('#rb-save').isEnabled(),false);
    assert.equal(posts.length,0);await page.locator('#rb-verified').check();await page.locator('#rb-save').click();
    await page.getByRole('alert').filter({hasText:'Stopped at row 2'}).waitFor();
    assert.equal(records.size,2);assert.equal(posts.length,2);
    const originalReferences=posts.map(r=>r.reference);
    await page.reload();await page.locator('#rb-app').waitFor({state:'visible'});
    assert.equal(posts.length,2);assert.equal(await page.locator('#rb-save').isEnabled(),false);
    await page.locator('#rb-verified').check();await page.locator('#rb-save').click();
    await page.locator('#rb-status').filter({hasText:'4 saved'}).waitFor();
    assert.equal(records.size,4);assert.equal(posts.length,5);assert.equal(posts[2].reference,originalReferences[1]);
    assert.deepEqual([...records.values()].map(r=>r.net_kg),[261.5,248,null,273.5]);
    assert.ok(posts.every(r=>r.plan.material==='WOODFREE - 66% BRIGHTNESS'));
    const downloadPromise=page.waitForEvent('download');await page.locator('#rb-backup').click();
    const download=await downloadPromise,backupPath=await download.path();const backup=JSON.parse(await fs.readFile(backupPath,'utf8'));
    assert.equal(backup.rows.filter(r=>r.unit).length,4);
    await page.locator('#rb-print').click();await page.locator('#rb-preview').waitFor({state:'visible'});
    assert.equal(await page.frameLocator('#rb-frame').locator('h1').count(),4);assert.equal(prints.length,4);
    assert.equal(posts.length,5);await page.locator('#rb-close').click();
    await page.setViewportSize({width:390,height:844});
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));
    const c2=await context(),p2=await c2.newPage();await p2.goto('https://reel-bulk.example.test/');await p2.locator('#rb-app').waitFor({state:'visible'});
    const changed=structuredClone(backup);changed.rows[0].plan.net_kg=999;
    await p2.locator('#rb-restore').setInputFiles({name:'changed.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(changed))});
    await p2.getByRole('alert').filter({hasText:'recovery plan differs'}).waitFor();assert.equal(posts.length,5);
    await p2.locator('#rb-restore').setInputFiles({name:'batch.json',mimeType:'application/json',buffer:Buffer.from(JSON.stringify(backup))});
    await p2.locator('#rb-status').filter({hasText:'Batch record restored'}).waitFor();
    assert.equal(await p2.locator('#rb-rows .saved').count(),4);assert.equal(posts.length,5);
    const cg=await context(true),pg=await cg.newPage();await pg.goto('https://reel-bulk.example.test/');
    await pg.locator('#rb-login').waitFor({state:'visible'});assert.equal(await pg.locator('#rb-app').isVisible(),false);assert.equal(posts.length,5);
    console.log('PASS: decimal validation, per-reel weights, inspection check, lost-response retry, reload recovery, multi-label print, mobile layout, backup restore and guest handling. No live stock writes.');
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1});
