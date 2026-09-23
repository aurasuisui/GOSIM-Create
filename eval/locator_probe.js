// 位置集合的**成员资格实测**（PLAN 裁决五要求：最小页面 + 每族 locator 各打一发；
// 零 token、本地；不靠推理）。
//
// 问的是：静态源码扫描时，哪些"位置"算命中哪一族？
//   named 族 = getByRole(8 个 role, {name}) ∪ getByText
//   field 族 = getByLabel ∪ getByPlaceholder ∪ getByRole({textbox,searchbox,combobox,spinbutton},{name})
//
// 用法：node eval/locator_probe.js  （在 repos/arc-bench 下用它的 node_modules 跑）
const { chromium } = require('playwright');

const RX = /probe/;   // 每个元素里都放同一串，看哪些 locator 能命中

const PAGE = `<!doctype html><html><body>
<h1 id="h1">probe</h1>                                  <!-- 文本节点 + heading 名 -->
<button id="btn" aria-label="probe"></button>            <!-- aria-label → 可访问名 -->
<button id="btnTitle" title="probe"></button>            <!-- title → 可访问名（回退） -->
<input id="inpPh" placeholder="probe">                   <!-- placeholder -->
<input id="inpName" name="probe">                        <!-- name 属性 -->
<input id="inpLabel" aria-label="probe">                 <!-- 输入类 + aria-label -->
<label for="inpFor">probe</label><input id="inpFor">     <!-- label[for] 关联 -->
<input id="inpBare">                                     <!-- 裸输入框 -->
<div id="divTitle" title="probe">x</div>                 <!-- 只有 title 的 div -->
</body></html>`;

const CHECKS = [
  // [说明, locator 构造]
  ['named: getByRole(button,name)', (p) => p.getByRole('button', { name: RX })],
  ['named: getByRole(heading,name)', (p) => p.getByRole('heading', { name: RX })],
  ['named: getByText', (p) => p.getByText(RX)],
  ['field: getByLabel', (p) => p.getByLabel(RX)],
  ['field: getByPlaceholder', (p) => p.getByPlaceholder(RX)],
  ['field: getByRole(textbox,name)', (p) => p.getByRole('textbox', { name: RX })],
  ['field: getByRole(combobox,name)', (p) => p.getByRole('combobox', { name: RX })],
];

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setContent(PAGE);
  console.log('每个 locator 命中的元素 id（空 = 不命中）：\n');
  for (const [label, build] of CHECKS) {
    const loc = build(page);
    const n = await loc.count();
    const ids = [];
    for (let i = 0; i < n; i += 1) {
      ids.push(await loc.nth(i).evaluate((el) => el.id || el.tagName));
    }
    console.log(`  ${label.padEnd(34)} ${n} 个  [${ids.join(', ')}]`);
  }
  await browser.close();
})();
