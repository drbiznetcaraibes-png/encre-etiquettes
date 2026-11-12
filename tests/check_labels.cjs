// Local browser regression: no remote searches, real shipped catalogue fixtures.
// APP_ACCESS_TOKEN must match the local server. Never point this at production.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH || undefined,headless:true,args:['--no-sandbox']});
 try{
 const context=await browser.newContext({viewport:{width:1440,height:1200},httpCredentials:{username:'encre',password:process.env.APP_ACCESS_TOKEN || 'browser-test'}});
 const page=await context.newPage(); const errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:'+(process.env.TEST_PORT || '8002'));
 const fixture=[...new Map(['phone','apple','xiaomi','samsung'].flatMap(name=>JSON.parse(fs.readFileSync(name+'_catalog.json','utf8')).phones).map(p=>[p.brand+p.model,p])).values()];
 await page.evaluate(phones=>{
  rows=phones.map((p,i)=>({...p,ref:p.brand+' '+p.model,capacity:p.capacities[0] || 'Non précisée',cash:i===0?450:1299.99,...calcFin(i===0?450:1299.99)}));
  draw();
 },fixture);
 await page.evaluate(()=>document.fonts.ready);
 await page.evaluate(()=>fitLabelText());
 assert.equal(await page.evaluate(()=>document.fonts.check('500 12px Inter')),true);
 const inspect=()=>page.evaluate(()=>[...document.querySelectorAll('.labelcard')].flatMap((card,i)=>{
  const issues=[];
  if(Math.abs(card.offsetWidth-88*96/25.4)>1 || Math.abs(card.offsetHeight-52*96/25.4)>1)issues.push('wrong dimensions');
  card.querySelectorAll('.spec-text').forEach(t=>{
   if(t.scrollHeight>t.parentElement.clientHeight+1 || t.scrollWidth>t.clientWidth+1)issues.push('spec clipped: '+t.textContent);
  });
  const cash=card.querySelector('.cash');
  card.querySelectorAll('.amount').forEach(amount=>{if(amount.scrollWidth>amount.clientWidth+1)issues.push('monthly amount overflow')});
  if(cash.scrollWidth>cash.clientWidth+1)issues.push('cash overflow');
  if(card.querySelector('.features').getBoundingClientRect().bottom>card.querySelector('.ref').getBoundingClientRect().top+1)issues.push('features overlap reference');
  return issues.map(issue=>({model:rows[i].model,issue}));
 }));
 await page.emulateMedia({media:'print'});
 assert.deepEqual(await inspect(),[]);
 await page.locator('.labelcard').first().screenshot({path:process.env.LABEL_PREVIEW || '/tmp/encre-label-preview.png'});
 const expected=await page.evaluate(()=>Math.max(1,Math.ceil(rows.length/10)));
 assert.equal(await page.locator('.sheet').count(),expected);
 // Eleven labels must produce two physical A4 pages; retain PDF for inspection.
 await page.evaluate(()=>{rows=rows.slice(0,11);draw()});
 assert.equal(await page.locator('.sheet').count(),2);
 await page.pdf({path:process.env.LABEL_PDF || '/tmp/encre-labels.pdf',preferCSSPageSize:true});
 await page.emulateMedia({media:'screen'});
 await page.setViewportSize({width:390,height:844});
 await page.evaluate(()=>draw());
 assert.deepEqual(await inspect(),[]);
 // Existing saved labels remain editable and use the exact financing coefficients.
 await page.locator('[data-act="edit"]').first().click();
 await page.fill('#cash','1000');
 assert.deepEqual(await page.locator('#p5,#p10,#p20').evaluateAll(es=>es.map(e=>e.value)),['208','107','56']);
 await page.click('#add');
 assert.equal(await page.locator('.labelcard img').count(),0);
 assert.equal(await page.locator('.labelcard .stripe,.labelcard .logo-phone').count(),0);
 await page.locator('.slot[role=button]').first().click();
 assert.equal(await page.locator('#labelPreview').evaluate(d=>d.open),true);
 assert.equal(await page.locator('#previewLabelContent .labelcard').count(),1);
 await page.locator('#closeLabelPreview').click();
 await page.waitForFunction(()=>document.getElementById('previewLabelContent').children.length===0);
 assert.equal(await page.locator('#previewLabelContent .labelcard').count(),0);
 // Keep all three grids; only eligible amounts may appear, including old imports.
 const cases=[
  [119.99,['','','']],[120,['','','']],[120.01,['25','','']],
  [187.03,['39','','']],[187.04,['39','21','']],
  [357.87,['75','39','']],[357.88,['75','39','21']],[1000,['208','107','56']]
 ];
 for(const [cash,expectedAmounts] of cases){
  await page.fill('#cash',String(cash));
  assert.deepEqual(await page.locator('#p5,#p10,#p20').evaluateAll(es=>es.map(e=>e.value)),expectedAmounts,'form at '+cash);
  await page.evaluate(cash=>{rows=[{...rows[0],cash,p5:'999',p10:'999',p20:'999'}];draw()},cash);
  assert.equal(await page.locator('.finrow').count(),3);
  assert.deepEqual(await page.locator('.finrow .count').allTextContents(),['×5','×10','×20']);
  assert.deepEqual(await page.locator('.finrow .amount').evaluateAll(es=>es.map(e=>e.querySelector('b')?.firstChild?.textContent||'')),expectedAmounts,'label at '+cash);
  assert.equal(await page.locator('.finrow .amount').evaluateAll(es=>es.some(e=>e.textContent.includes('—'))),false);
 }
 await page.evaluate(()=>{rows=[{...rows[0],cash:120,p5:'25',p10:'13',p20:'7'}];persist()});
 await page.reload();
 assert.deepEqual(await page.locator('.finrow .amount').allTextContents(),['','','']);
 await page.emulateMedia({media:'print'});
 assert.equal(await page.locator('.finrow').count(),3);
 assert.deepEqual(await page.locator('.finrow .amount').allTextContents(),['','','']);
 assert.deepEqual(errors,[]);
 console.log('Labels OK: '+fixture.length+' catalogue models, 88×52 mm, two A4 pages, mobile, saved-label editing, financing');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
