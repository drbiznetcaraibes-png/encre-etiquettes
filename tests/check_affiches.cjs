// Test de non-régression des affiches A4 TV/consoles.
// Exécuter sur un serveur LOCAL authentifié (jamais en production).
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox']});
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1100},httpCredentials:{username:'encre',password:process.env.APP_ACCESS_TOKEN||'browser-test'}});
  const page=await context.newPage(),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  const consoles=[
   {brand:'PlayStation',model:'PlayStation 5 Pro',reference:'CFI-7000',variant:'',
    specs:{storage:'SSD 2 To',video:'4K',technology:'PSSR',included:'DualSense'},available_capacities:[]},
   {brand:'Nintendo',model:'Switch 2',reference:'BEE-001',variant:'',
    specs:{storage:'256 Go',video:'Full HD',technology:'4K en mode TV',included:'Joy-Con 2'},available_capacities:[]},
  ];
  await page.route('**/api/products?*',async route=>{
   const params=new URL(route.request().url()).searchParams;
   assert.equal(params.get('category'),'console');
   const query=params.get('query').toLowerCase();
   const products=consoles.filter(p=>[p.brand,p.model,p.reference].some(text=>text.toLowerCase().includes(query)));
   await route.fulfill({json:{products}});
  });
  await page.goto('http://127.0.0.1:'+(process.env.TEST_PORT||'8002')+'/affiches');
  await page.evaluate(()=>localStorage.removeItem('encre_affiches_v1'));
  await page.reload();
  assert.equal(await page.locator('.sheet').count(),0);
  await page.fill('#cash','799');
  await page.click('#add');
  assert.match(await page.locator('#status').textContent(),/Sélectionne une console/);
  await page.fill('#consoleQuery','CFI-7000');
  await page.locator('#consoleQuery').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#name').value==='PlayStation 5 Pro');
  assert.equal(await page.inputValue('#name'),'PlayStation 5 Pro');
  await page.fill('#cash','799');
  await page.click('#add');
  assert.equal(await page.locator('.poster').count(),1);
  assert.equal(await page.locator('.sheet').count(),1);
  assert.deepEqual(await page.locator('.poster .month span').allTextContents(),['166 €','86 €','45 €']);
  assert.equal(await page.locator('.poster img').count(),0);
  assert.equal(await page.locator('.poster.console .spec-item').count(),4);
  assert.equal(await page.locator('.poster.console .spec-value').last().textContent(),'DualSense');

  await page.selectOption('#category','tv');
  assert.equal(await page.locator('#consoleSearch').isHidden(),true);
  assert.equal(await page.locator('#tvSizeFields').isVisible(),true);
  await page.fill('#name','Téléviseur de test QLED');
  await page.fill('#variant','Neo QLED');
  await page.fill('#reference','TQ55XXXXX');
  await page.fill('#inches','55');
  await page.fill('#centimeters','139');
  await page.fill('#s0','Mini LED');
  await page.fill('#s1','4K UHD');
  await page.fill('#s2','144 Hz');
  await page.fill('#s3','Tizen');
  await page.fill('#cash','999');
  await page.click('#add');
  assert.equal(await page.locator('.poster.tv').count(),1);
  assert.equal(await page.locator('.sheet').count(),1);
  assert.equal(await page.locator('.tv-size strong').textContent(),'55″');
  assert.deepEqual(await page.locator('.poster.tv .month span').allTextContents(),['208 €','107 €','56 €']);
  await page.emulateMedia({media:'print'});
  const dims=await page.locator('.poster').first().evaluate(el=>({width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}));
  const pxPerMm=96/25.4;
  assert.ok(Math.abs(dims.width-190*pxPerMm)<2,'largeur 190 mm');
  assert.ok(Math.abs(dims.height-132*pxPerMm)<2,'hauteur 132 mm');
  const sheet=await page.locator('.sheet').first().boundingBox();
  assert.ok(Math.abs(sheet.width-210*pxPerMm)<2,'largeur A4');
  assert.ok(Math.abs(sheet.height-297*pxPerMm)<2,'hauteur A4');
  assert.equal(await page.locator('.cut-slot:not(.empty) .mark').count(),8,'quatre repères par affiche');
  await page.pdf({path:process.env.AFFICHE_PDF||'/tmp/encre-affiches-test.pdf',preferCSSPageSize:true});
  await page.emulateMedia({media:'screen'});

  await page.locator('#items li').first().getByText('Modifier').click();
  await page.fill('#cash','120');
  await page.click('#add');
  assert.deepEqual(await page.locator('.poster.console .month span').allTextContents(),['','','']);
  await page.reload();
  assert.equal(await page.locator('.poster').count(),2,'sauvegarde navigateur');

  await page.selectOption('#category','console');
  await page.locator('#consoleSearch button').click();
  await page.locator('#consoleResults option').nth(2).waitFor({state:'attached'});
  await page.selectOption('#consoleResults',{label:'Nintendo Switch 2 · BEE-001'});
  await page.fill('#cash','499');
  await page.click('#add');
  assert.equal(await page.locator('.sheet').count(),2,'trois affiches = deux A4');
  assert.equal(await page.locator('.poster').count(),3);
  assert.equal(await page.locator('.cut-slot.empty').count(),1);
  await page.locator('#items li').last().getByText('Supprimer').click();
  assert.equal(await page.locator('.sheet').count(),1);
  assert.deepEqual(errors,[]);
  console.log('Affiches OK : TV + console, A4 portrait, 190 × 132 mm, sauvegarde, financement, 2/page');
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exit(1)});
