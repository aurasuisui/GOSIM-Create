const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const p = await b.newPage();
  await p.goto('https://arc-bench.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(2500);
  console.log('url:', p.url());
  const inputs = await p.evaluate(() => Array.from(document.querySelectorAll('input')).map(e => (e.type||'') + ':' + (e.name||e.id||'') + ':' + (e.placeholder||'')));
  console.log('inputs:', JSON.stringify(inputs));
  const btns = await p.evaluate(() => Array.from(document.querySelectorAll('button')).map(e => (e.textContent||'').trim()).filter(Boolean).slice(0,8));
  console.log('buttons:', JSON.stringify(btns));
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });