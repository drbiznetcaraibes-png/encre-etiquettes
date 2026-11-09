const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'playwright');const assert=require('node:assert/strict');
(async()=>{const b=await chromium.launch({executablePath:process.env.CHROMIUM_PATH || undefined,headless:true,args:['--no-sandbox']});try{
 const ctx=await b.newContext({httpCredentials:{username:'encre',password:process.env.APP_ACCESS_TOKEN || 'browser-test'}});const p=await ctx.newPage();await p.goto('http://127.0.0.1:'+(process.env.TEST_PORT || '8002'));
 await p.fill('#reference','iPhone 16');await p.fill('#capacityGb','256');await p.click('#search');await p.waitForFunction(()=>selected?.model==='iPhone 16');
 assert.equal(await p.evaluate(()=>selected.capacity),'256 Go');await p.fill('#cash','450');await p.click('#add');assert.equal(await p.locator('.storage').textContent(),'256 Go');
 await p.locator('[data-act="edit"]').click();assert.equal(await p.inputValue('#capacityGb'),'256');await p.fill('#capacityGb','512');await p.click('#add');assert.equal(await p.locator('.storage').textContent(),'512 Go');
 await p.locator('[data-act="edit"]').click();await p.fill('#capacityGb','64');await p.click('#add');assert.equal(await p.locator('.storage').textContent(),'512 Go');
 await p.fill('#capacityGb','-1');assert.equal(await p.locator('#capacityGb').evaluate(e=>e.checkValidity()),false);
 await p.click('#clearForm');assert.equal(await p.inputValue('#capacityGb'),'');
 await p.evaluate(()=>setSelected({brand:'GOOGLE',model:'Pixel 9',capacity:'Non précisée',specs:{ecran:'OLED',photo:'50 Mpx',reseau:'5G'}},'GOOGLE PIXEL 9','test'));
 await p.fill('#capacityGb','1024');assert.equal(await p.evaluate(()=>selected.capacity),'1 To');assert.equal(await p.evaluate(()=>selected.capacity_confirmed),false);assert.equal(await p.evaluate(()=>selected.capacity_manually_entered),true);
 await p.fill('#capacityGb','');assert.equal(await p.evaluate(()=>selected.capacity),'Non précisée');
 console.log('Capacity input OK: before search, editing, invalid variants, negative input, reset, TB conversion, manual provenance');
}finally{await b.close()}})().catch(e=>{console.error(e);process.exit(1)});
