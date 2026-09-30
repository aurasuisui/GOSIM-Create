const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const p = await b.newPage();
  await p.goto('https://arc-bench.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(1200);
  await p.fill('#login-email', '2436448088@qq.com');
  await p.fill('#login-password', 'xbao2436');
  await p.click('button:has-text("Login")');
  await p.waitForTimeout(2500);
  await p.goto('https://arc-bench.com/runs/b3b6263b2053', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForTimeout(8000);
  // 1) Stdout 标签
  await p.getByText('Stdout', { exact: true }).first().click();
  await p.waitForTimeout(5000);
  let body = await p.evaluate(() => document.body.innerText);
  console.log('=== after Stdout click, len', body.length, '===');
  console.log(body.slice(0, 1800));
  // 2) 展开 requirements 目录
  const req = p.getByText('requirements', { exact: true }).first();
  if (await req.count()) {
    await req.click();
    await p.waitForTimeout(4000);
    body = await p.evaluate(() => document.body.innerText);
    const i = body.indexOf('requirements');
    console.log('=== after requirements click ===');
    console.log(body.slice(Math.max(0, i - 100), i + 1200));
  } else { console.log('no requirements node'); }
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });