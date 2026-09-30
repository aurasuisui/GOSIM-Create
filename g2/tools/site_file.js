const { chromium } = require('playwright');
(async () => {
  const b = await chromium.launch({ executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe' });
  const p = await b.newPage();
  const calls = [];
  p.on('request', r => { const u = r.url(); if (u.includes('/api/runs/')) calls.push(r.method() + ' ' + u.replace('https://arc-bench.com','')); });
  await p.goto('https://arc-bench.com/login', { waitUntil: 'domcontentloaded', timeout: 60000 });
  await p.waitForTimeout(1200);
  await p.fill('#login-email', '2436448088@qq.com');
  await p.fill('#login-password', 'xbao2436');
  await p.click('button:has-text("Login")');
  await p.waitForTimeout(2500);
  await p.goto('https://arc-bench.com/runs/b3b6263b2053', { waitUntil: 'domcontentloaded', timeout: 90000 });
  await p.waitForTimeout(8000);
  try { await p.getByText('File', { exact: true }).first().click({ timeout: 8000 }); } catch (e) {}
  await p.waitForTimeout(3000);
  calls.length = 0;
  // 展开 requirements 目录（点它的父节点/箭头）
  const el = p.locator('text=prerequisites.md').first();
  console.log('prereq visible:', await p.locator('text=prerequisites.md').count());
  if (await el.count()) { try { await el.click({ timeout: 8000 }); } catch (e) { console.log('click err', e.message.slice(0,60)); } }
  await p.waitForTimeout(5000);
  console.log('=== calls after clicking prerequisites.md ===');
  console.log([...new Set(calls)].slice(0, 12).join('\n'));
  const body = await p.evaluate(() => document.body.innerText);
  const i = body.indexOf('prerequisites');
  console.log('=== body around prerequisites ===');
  console.log(body.slice(Math.max(0,i-200), i + 900));
  await b.close();
})().catch(e => { console.error('FAILED', e.message); process.exit(1); });