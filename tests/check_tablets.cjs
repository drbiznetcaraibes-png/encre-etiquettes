// Run on a local authenticated instance. Mock the product catalogue only.
const assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');

(async()=>{
 const browser=await chromium.launch({
  executablePath:process.env.CHROMIUM_PATH||undefined,headless:true,args:['--no-sandbox'],
 });
 try{
  const context=await browser.newContext({
   httpCredentials:{username:'encre',password:process.env.APP_ACCESS_TOKEN||'browser-test'},
  });
  const page=await context.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  const tablet={
   brand:'Samsung',model:'Galaxy Tab S9+',reference:'SM-X810',variant:'',
   specs:{processeur:'Snapdragon 8 Gen 2',ecran:'AMOLED 12,4″',ram:'12 Go'},
   available_capacities:['256 Go','512 Go'],
  };
  await page.route('**/api/products?*',async route=>{
   const params=new URL(route.request().url()).searchParams;
   assert.equal(params.get('category'),'tablet');
   const products=params.get('query')==='SM-X810'?[tablet]:[];
   await route.fulfill({json:{products}});
  });
  await page.goto('http://127.0.0.1:'+(process.env.TEST_PORT||'8002'));
  await page.selectOption('#labelCategory','tablet');
  assert.equal(await page.locator('#phoneSearch').isHidden(),true);
  await page.fill('#tabletQuery','inconnu');
  await page.locator('#tabletSearch button').click();
  await page.getByText('Aucun produit trouvé.').waitFor();
  await page.fill('#tabletQuery','SM-X810');
  await page.locator('#tabletQuery').press('Enter');
  await page.locator('#capacityChoice').waitFor();
  assert.deepEqual(await page.locator('#capacityChoice option').allTextContents(),[
   'Choisir le stockage','256 Go','512 Go',
  ]);
  await page.fill('#cash','1000');
  await page.click('#add');
  assert.equal(await page.locator('.labelcard').count(),0,'choix du stockage obligatoire');
  await page.selectOption('#capacityChoice','512 Go');
  await page.fill('#tabletQty','2');
  await page.click('#add');
  assert.equal(await page.locator('.labelcard').count(),2);
  const card=page.locator('.labelcard').first();
  assert.equal(await card.locator('.storage').textContent(),'512 Go');
  assert.match(await card.locator('.features').textContent(),/Snapdragon.*AMOLED/);
  assert.deepEqual(await card.locator('.amount b').allTextContents(),['208€','107€','56€']);
  await page.emulateMedia({media:'print'});
  const dims=await card.boundingBox(),mm=96/25.4;
  assert.ok(Math.abs(dims.width-88*mm)<2&&Math.abs(dims.height-52*mm)<2);
  await page.emulateMedia({media:'screen'});

  await page.reload();
  assert.equal(await page.locator('.labelcard').count(),2);
  assert.equal(await page.locator('#phoneSearch').isVisible(),true);
  await page.locator('#list li').first().getByText('Modifier').click();
  assert.equal(await page.inputValue('#labelCategory'),'tablet');
  assert.equal(await page.inputValue('#capacityChoice'),'512 Go');
  await page.selectOption('#capacityChoice','256 Go');
  await page.fill('#cash','120');
  await page.click('#add');
  assert.deepEqual(await page.locator('.labelcard .storage').allTextContents(),['256 Go','512 Go']);
  assert.equal(await card.locator('.amount b').count(),0);
  await page.locator('#list li').last().locator('[data-act=del]').click();
  assert.equal(await page.locator('.labelcard').count(),1);
  await page.selectOption('#labelCategory','phone');
  assert.equal(await page.locator('#capacityGb').isVisible(),true);
  assert.deepEqual(errors,[]);
  console.log('Tablettes OK : recherche, stockage, quantité, dimensions, financement, modification et suppression');
 }finally{await browser.close()}
})().catch(error=>{console.error(error);process.exit(1)});
