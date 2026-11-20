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
   {brand:'PLAYSTATION',model:'PS5 Pro — 2 To Digital',reference:'CFI-7000',variant:'Édition numérique',
    specs:[{label:'Stockage',value:'SSD 2 To'},{label:'Image',value:'4K'},
           {label:'Technologie',value:'PSSR'},{label:'Lecteur',value:'Accessoire optionnel'}],
    available_capacities:['2 To'],verification_status:'vérifié'},
   {brand:'Nintendo',model:'Switch 2',reference:'BEE-001',variant:'',
    specs:[{label:'Stockage',value:'256 Go'},{label:'Image',value:'Full HD'},
           {label:'Technologie',value:'4K en mode TV'},{label:'Inclus',value:'Joy-Con 2'}],
    available_capacities:['256 Go'],verification_status:'vérifié'},
  ];
  const tv={brand:'Samsung',model:'Téléviseur de test QLED',reference:'TQ55XXXXX',variant:'Neo QLED',
   inches:55,available_capacities:[],verification_status:'vérifié',sources:[{url:'https://www.samsung.com/fr/tvs/'}],
   specs:[{label:'Technologie écran',value:'Mini LED'},{label:'Résolution',value:'4K UHD'},
          {label:'Fréquence native',value:'144 Hz'},{label:'Smart TV',value:'Tizen'}]};
  const tvQueries=[];
  await page.route('**/api/products/lookup',async route=>{
   assert.equal(route.request().method(),'POST');
   const {query,category}=route.request().postDataJSON();
   if(category==='tv'){
    tvQueries.push(query);
    if(query==='Samsung QLED'){
     await route.fulfill({json:{products:[],choices:['Samsung QLED 55 pouces','Samsung QLED 65 pouces']}});
    }else{
     assert.equal(query,'Samsung QLED 55 pouces');
     await route.fulfill({json:{products:[tv]}});
    }
   }else{
    assert.equal(category,'console');
    const products=consoles.filter(p=>[p.brand,p.model,p.reference].some(text=>text.toLowerCase().includes(query.toLowerCase())));
    await route.fulfill({json:{products}});
   }
  });
  await page.goto('http://127.0.0.1:'+(process.env.TEST_PORT||'8002')+'/affiches');
  await page.evaluate(()=>localStorage.removeItem('encre_affiches_v1'));
  await page.reload();
  assert.equal(await page.locator('.sheet').count(),0);
  await page.fill('#cash','799');
  await page.click('#add');
  assert.match(await page.locator('#status').textContent(),/Sélectionne un produit/);
  await page.fill('#posterQuery','CFI-7000');
  await page.locator('#posterQuery').press('Enter');
  await page.locator('#posterProduct').getByText(/PLAYSTATION PS5 Pro/).waitFor();
  assert.equal(await page.inputValue('#cash'),'799');
  await page.fill('#cash','799');
  await page.click('#add');
  assert.equal(await page.locator('.poster').count(),1);
  assert.equal(await page.locator('.sheet').count(),1);
  assert.deepEqual(await page.locator('.poster .month span').allTextContents(),['166 €','86 €','45 €']);
  assert.equal(await page.locator('.poster img').count(),0);
  assert.equal(await page.locator('.poster.console .spec-item').count(),4);
  assert.equal(await page.locator('.poster.console .spec-label').last().textContent(),'Lecteur');
  assert.equal(await page.locator('.poster.console .spec-value').last().textContent(),'Accessoire optionnel');

  await page.selectOption('#category','tv');
  await page.fill('#posterQuery','Samsung QLED');
  await page.locator('#posterQuery').press('Enter');
  await page.locator('#posterResults option').nth(2).waitFor({state:'attached'});
  await page.selectOption('#posterResults',{label:'Samsung QLED 55 pouces'});
  await page.locator('#posterProduct').getByText(/Téléviseur de test QLED/).waitFor();
  assert.deepEqual(tvQueries,['Samsung QLED','Samsung QLED 55 pouces']);
  assert.equal(await page.locator('#posterProduct a').getAttribute('href'),'https://www.samsung.com/fr/tvs/');
  assert.equal(await page.locator('#inches,#name,#reference,#s0').count(),0,'aucun champ technique manuel');
  assert.equal(await page.inputValue('#cash'),'');
  await page.fill('#cash','999');
  await page.click('#add');
  assert.equal(await page.locator('.poster.tv').count(),1);
  assert.equal(await page.locator('.sheet').count(),1);
  assert.equal(await page.locator('.tv-size strong').textContent(),'55″');
  assert.equal(await page.locator('.poster.tv .spec-item').count(),4);
  assert.deepEqual(await page.locator('.poster.tv .month span').allTextContents(),['208 €','107 €','56 €']);
  await page.emulateMedia({media:'print'});
  const dims=await page.locator('.poster').first().evaluate(el=>({width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}));
  const pxPerMm=96/25.4;
  assert.ok(Math.abs(dims.width-190*pxPerMm)<2,'largeur 190 mm');
  assert.ok(Math.abs(dims.height-132*pxPerMm)<2,'hauteur 132 mm');
  const sheet=await page.locator('.sheet').first().boundingBox();
  assert.ok(Math.abs(sheet.width-210*pxPerMm)<2,'largeur A4');
  assert.ok(Math.abs(sheet.height-297*pxPerMm)<2,'hauteur A4');
  const marks=page.locator('.cut-slot:not(.empty) .mark');
  assert.equal(await marks.count(),8,'quatre repères par affiche');
  const posterBox=await page.locator('.poster').first().boundingBox();
  const topLeft=await marks.first().boundingBox();
  assert.ok(topLeft.x>=sheet.x&&topLeft.y>=sheet.y,'repère dans la feuille');
  assert.ok(topLeft.x+topLeft.width<posterBox.x&&topLeft.y+topLeft.height<posterBox.y,'repère en dehors de l’affiche');
  await page.pdf({path:process.env.AFFICHE_PDF||'/tmp/encre-affiches-test.pdf',preferCSSPageSize:true});
  await page.emulateMedia({media:'screen'});

  await page.locator('#items li').first().getByText('Modifier').click();
  await page.fill('#cash','120');
  await page.click('#add');
  assert.deepEqual(await page.locator('.poster.console .month span').allTextContents(),['','','']);
  await page.reload();
  assert.equal(await page.locator('.poster').count(),2,'sauvegarde navigateur');
  assert.equal(await page.locator('.poster.console .spec-label').last().textContent(),'Lecteur');

  await page.selectOption('#category','console');
  await page.locator('#posterSearch button').click();
  await page.locator('#posterResults option').nth(2).waitFor({state:'attached'});
  await page.selectOption('#posterResults',{label:'Nintendo Switch 2 · BEE-001 · 256 Go'});
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
