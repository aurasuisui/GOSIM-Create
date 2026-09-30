const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const p = await b.newPage();
  const calls = [];
  p.on('request', r => { const u = r.url(); if (u.includes('/api/')) calls.push(r.method() + ' ' + u.replace('https://arc-bench.com','')); });
  await p.goto('https://arc-bench.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(1200);
  await p.fill('#login-email', '2436448088@qq.com');
  await p.fill('#login-password', 'xbao2436');
  await p.click('button:has-text("Login")');
  await p.waitForTimeout(2500);
  calls.length = 0;
  await p.goto('https://arc-bench.com/runs/b3b6263b2053', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForTimeout(9000);
  console.log('=== API calls on run page ===');
  console.log([...new Set(calls)].join('\n').slice(0, 2500));
  // 点 File 标签，再抓一轮
  try { await p.getByText('File', { exact: true }).first().click({ timeout: 8000 }); } catch (e) { console.log('file tab click failed'); }
  await p.waitForTimeout(4000);
  console.log('=== after File tab ===');
  console.log([...new Set(calls)].join('\n').slice(0, 2500));
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });