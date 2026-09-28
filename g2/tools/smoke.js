/**
 * 冒烟探针：**16 分钟的判分之前，先花 10 秒问"应用真的能用吗"**。
 *
 * 为什么必须有它（旧管线的教训，见 .rebuild/LESSONS.md）：
 *   实测一整轮 0/6 里 5 条是 ERR_CONNECTION_REFUSED —— 应用根本没起来，
 *   而当时的闭环是静态的（L1）+ 模型判断的（自检），**两者都不会把应用真的起起来点一下**。
 *   本轮的同类形态：首页没有 Login 入口 → 7 条判据卡在导航超时，而静态检查全绿。
 *
 * 用法：node smoke.js <baseUrl> <fixtures.json> <nav_targets.json> <assert_texts.json>
 */
const { chromium } = require('playwright');
const fs = require('fs');

const [url, fixturesPath, navPath, assertPath] = process.argv.slice(2);
const fixtures = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'));
const nav = JSON.parse(fs.readFileSync(navPath, 'utf8'));
const asserts = JSON.parse(fs.readFileSync(assertPath, 'utf8'));

function flattenAuth(node) {
  const out = {};
  const walk = (n) => {
    if (!n || typeof n !== "object") return;
    for (const [k, v] of Object.entries(n)) {
      if (v && typeof v === "object") walk(v);
      else if (typeof v === "string" && !(k in out)) out[k] = v;
    }
  };
  walk(node);
  return out;
}

function pickAuth(fx) {
  const candidates = [fx.auth, fx.user, fx.account, fx.login, fx].filter(Boolean);
  for (const c of candidates) {
    const flat = flattenAuth(c);
    const email = flat.email || flat.username || flat.nickname;
    const password = flat.password;
    if (email && password) return { email, password, username: flat.nickname || flat.username || email };
  }
  return null;
}

(async () => {
  const report = { errors: [], warnings: [], ok: [] };
  const browser = await chromium.launch({
    executablePath: 'C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe',
  });
  const page = await browser.newPage();
  const httpProblems = [];
  page.on("response", (r) => { if (r.status() >= 400) httpProblems.push(r.status() + " " + r.url()); });

  await page.goto(url, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  const bodyText = await page.evaluate(() => document.body.innerText);
  report.ok.push("home loaded, " + bodyText.length + " chars");

  // ① 未登录时必须有 Login 入口（判据第一步）
  const loginBtn = page.getByRole("button", { name: /^Login$/i });
  const loginLink = page.getByRole("link", { name: /^Login$/i });
  const nBtn = await loginBtn.count();
  const nLink = await loginLink.count();
  if (nBtn + nLink === 0) report.errors.push("未登录首页没有名为 Login 的按钮/链接（判据第一步就会卡死）");
  else report.ok.push("Login 入口：button=" + nBtn + " link=" + nLink);

  // ② 登录流程
  const auth = pickAuth(fixtures);
  if (!auth) {
    report.warnings.push("夹具里没找到 email/password，跳过登录");
  } else {
    if (nLink) await loginLink.first().click().catch(() => {});
    else if (nBtn) await loginBtn.first().click().catch(() => {});
    await page.waitForTimeout(1200);
    const fill = async (label, value) => {
      for (const loc of [page.getByLabel(label), page.getByPlaceholder(label), page.getByRole("textbox", { name: label })]) {
        if (await loc.count()) { await loc.first().fill(value).catch(() => {}); return true; }
      }
      return false;
    };
    const okEmail = await fill("Email address", auth.email) || await fill("Email", auth.email);
    const okPass = await fill("Password", auth.password);
    report.ok.push("登录表单：email=" + okEmail + " password=" + okPass);
    // ⚠️ 提交按钮必须**限定在登录表单里**再点 —— 与真 helpers 同源：
    //     `page.getByRole('form', { name: /^Login form$/i }).getByRole('button', { name: /^Login$/i })`
    // 踩过的坑：导航栏里也有一个 Login 按钮，`getByRole(...).first()` 命中的是它 →
    // 点了个"跳转到登录页"的空操作 → 探针误判"登录失败"（**假阴性同样贵：会去修不存在的问题**）。
    const form = page.getByRole("form", { name: /^Login form$/i });
    let submit = form.getByRole("button", { name: /^Login$/i });
    if (!(await submit.count())) submit = page.locator('form').getByRole("button", { name: /^Login$/i });
    if (!(await submit.count())) submit = page.getByRole("button", { name: /^(Login|Sign in|Sign In)$/i });
    if (await submit.count()) await submit.first().click().catch(() => {});
    await page.waitForTimeout(1800);
    const after = await page.evaluate(() => document.body.innerText);
    if (/invalid|incorrect|failed|error/i.test(after)) report.warnings.push("登录后页面出现错误字样");
    const hasUser = auth.username && after.includes(auth.username);
    if (!hasUser) report.warnings.push("登录后页面上看不到用户名 " + auth.username);
    else report.ok.push("登录后显示用户名");
  }

  // ③ 断言文本是否可见
  const afterLogin = await page.evaluate(() => document.body.innerText);
  const missingTexts = asserts.filter((t) => !afterLogin.includes(t));
  if (missingTexts.length) report.warnings.push("断言文本缺失：" + missingTexts.slice(0, 6).join(" / "));
  else report.ok.push("断言文本齐全（" + asserts.length + " 条）");

  // ④ 导航契约：每个名字能不能点到
  const nameList = [...new Set(nav.map((n) => n.name))];
  const seenNames = new Set();
  const unreachable = [];
  for (const item of nav) {
    const name = item.name;
    if (seenNames.has(name)) continue;
    seenNames.add(name);
    const rx = new RegExp("^" + name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&") + "$", "i");
    // 按**目标自己的 role** 查：checkbox/form 之类不是 button/link，用 button 查必然误报
    // （假阴性同样贵：会去修一个不存在的问题）。
    const role = String(item.role || "button");
    let count = await page.getByRole(role, { name: rx }).count();
    if (!count) count = await page.getByRole("button", { name: rx }).count();
    if (!count) count = await page.getByRole("link", { name: rx }).count();
    if (!count) count = await page.getByRole("tab", { name: rx }).count();
    if (!count) unreachable.push(name + "[" + role + "]");
  }
  if (unreachable.length) report.errors.push("导航不可达（" + unreachable.length + "/" + nameList.length + "）：" + unreachable.join(" / "));
  else report.ok.push("导航契约全部可达（" + nameList.length + " 个名字）");

  const serious = httpProblems.filter((p) => !/401/.test(p));
  if (serious.length) report.warnings.push("非 401 的 HTTP 问题：" + serious.slice(0, 5).join(" | "));

  await browser.close();
  console.log(JSON.stringify(report, null, 1));
  process.exit(report.errors.length ? 1 : 0);
})().catch((e) => { console.error("SMOKE FAILED", e.message); process.exit(2); });