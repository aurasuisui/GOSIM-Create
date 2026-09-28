const app = require('./app');
const { seedDatabase } = require('./database/seed_db');

/**
 * 由管线生成（确定性，零 LLM）：后端入口 + 防崩护栏 + **种子再断言**。
 *
 * 为什么要"再断言"（2026-09-28 实测）：产物里 `seed.sql` 明明是好的、schema 也对、
 * bootstrap 报告 0 错误，但**运行期 `workbooks` 表是空的**（cells 有、workbooks 没有）——
 * 说明启动早期有别的初始化在竞争同一张表。判据要的既有记录因此不存在 → 整批失败。
 * 兜底：**监听成功后再幂等地跑一次 seed.sql**（`INSERT OR IGNORE`），让种子最终一定在位。
 */
process.on('unhandledRejection', (reason) => {
  console.error('[guard] unhandledRejection:', (reason && reason.stack) || reason);
});
process.on('uncaughtException', (error) => {
  console.error('[guard] uncaughtException:', (error && error.stack) || error);
});

const defaultPort = 3000;
const port = Number(process.env.PORT || defaultPort);

const server = app.listen(port, () => {
  console.log(`Backend listening at http://127.0.0.1:${port}`);
  // 幂等再断言（延迟一点，让应用自己的初始化先跑完）
  setTimeout(() => {
    Promise.resolve()
      .then(() => seedDatabase())
      .then((r) => console.log('[db] seed re-asserted:', JSON.stringify(r || {})))
      .catch((e) => console.error('[db] seed re-assert failed:', (e && e.message) || e));
  }, 1500);
});

server.on('error', (error) => {
  console.error('[guard] server error:', (error && error.stack) || error);
});
