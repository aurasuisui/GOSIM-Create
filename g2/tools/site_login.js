const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const p = await b.newPage();
  await p.goto('https://arc-bench.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(1500);
  await p.fill('#login-email', '2436448088@qq.com');
  await p.fill('#login-password', 'xbao2436');
  await p.click('button:has-text("Login")');
  await p.waitForTimeout(4000);
  console.log('after login url:', p.url());
  // 找含 run 的链接
  const links = await p.evaluate(() => Array.from(document.querySelectorAll('a')).map(a => a.getAttribute('href')).filter(h => h && (h.includes('run') || h.includes('competition') || h.includes('submission'))).slice(0, 20));
  console.log('links:', JSON.stringify(links));
  const body = await p.evaluate(() => document.body.innerText.replace(/\n{2,}/g, '\n'));
  console.log('body head:', body.slice(0, 700));
  await p.screenshot({ path: 'g2/runs/site-home.png', fullPage: false });
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });