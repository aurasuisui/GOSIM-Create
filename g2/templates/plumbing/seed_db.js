/**
 * 由管线生成（确定性，零 LLM）：**种子数据的唯一入口**。
 *
 * 为什么必须由管线拥有（2026-09-28 实测）：这个文件原本是模型写的，而它**自己也往同一批表里
 * 塞默认数据**（`INSERT OR IGNORE INTO workbooks ...`），并且在"定向修复"里被重写过。
 * 结果是：我们按需求正文抽出来的评测预置数据（`Q3 Sales` / `Sheet1` / `A1=Region`）
 * **要么被覆盖、要么整个库是空的**（实测 `/api/workbooks` 返回 `[]`）。
 * 现在它只做一件事：执行 `seed.sql`（内容完全由管线决定）。
 */
const { execSqlFile } = require('./bootstrap');

async function seedDatabase(database) {
  if (!database) {
    const { getDb } = require('./index');
    database = await getDb();
  }
  return execSqlFile(database, 'seed.sql');
}

module.exports = seedDatabase;
module.exports.seedDatabase = seedDatabase;
module.exports.seed = seedDatabase;
