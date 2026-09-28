/**
 * 由管线生成（确定性，零 LLM）：在数据库初始化之后执行 schema.sql 与 seed.sql。
 *
 * 为什么由管线给、不交给模型写：实测模型既不建表也不写种子，
 * 而外部判据大量依赖「已存在的记录」（例如从列表里点某个名字进详情页）。
 * 两个文件都是纯 SQL，代码生成比让模型猜更便宜，而且不会漏。
 */
const fs = require('fs');
const path = require('path');

async function execSqlFile(database, filename) {
  const file = path.join(__dirname, filename);
  if (!fs.existsSync(file)) return { file: filename, skipped: true };
  const sql = fs.readFileSync(file, 'utf8');
  if (!sql.trim()) return { file: filename, empty: true };
  // 🔴 **逐条执行**：实测一条语句出错（例如 duplicate column name）会让 `exec` 整体中止，
  //    之后所有表都建不出来 → 接口全是 `no such table`（整个应用废掉）。
  //    逐条跑 + 单独 try/catch，坏的那条不影响其余。
  const statements = sql.split(/;\s*\n/).map((s) => s.trim()).filter((s) => s && !s.startsWith('--'));
  const errors = [];
  for (const stmt of statements) {
    await new Promise((resolve) => {
      database.exec(stmt.endsWith(';') ? stmt : stmt + ';', (err) => {
        if (err) errors.push(String(err.message || err));
        resolve();
      });
    });
  }
  return { file: filename, bytes: sql.length, statements: statements.length, errors: errors.slice(0, 5) };
}

async function bootstrap(database) {
  const results = [];
  for (const name of ['schema.sql', 'seed.sql']) {
    try {
      results.push(await execSqlFile(database, name));
    } catch (error) {
      results.push({ file: name, error: String((error && error.message) || error) });
    }
  }
  // 🔴 **把每条失败喊出来**（2026-09-28 实测教训）：逐条执行是必要的（一条坏语句不该废掉整库），
  //    但它把失败**吞成了静默** —— 实测产物里 `worksheets` 表根本没建出来、`workbooks` 是空的，
  //    而平台日志、接口返回、前端表现**全都正常**，只能靠事后手工查 sqlite 才发现。
  //    这类"静默丢数据"直接对应判据里"列表里找不到那条记录"。
  //    现在：写到 stdout（平台会收集 agent 日志）+ 落一份 `.arc/db-bootstrap.json`。
  try {
    const fs = require('fs');
    const pathMod = require('path');
    const problems = [];
    for (const r of results) {
      if (r && r.error) problems.push(`${r.file}: ${r.error}`);
      for (const e of (r && r.errors) || []) problems.push(`${r.file}: ${e}`);
    }
    if (problems.length) {
      console.error('[db] bootstrap 有失败语句（下面每条都会导致数据缺失）:');
      for (const p of problems.slice(0, 20)) console.error('[db]   ' + p);
    } else {
      console.log('[db] schema + seed 全部语句执行成功');
    }
    try {
      const arcDir = pathMod.resolve(__dirname, '../../../.arc');
      fs.mkdirSync(arcDir, { recursive: true });
      fs.writeFileSync(pathMod.join(arcDir, 'db-bootstrap.json'),
        JSON.stringify({ results, problems }, null, 2));
    } catch (e) { /* 落盘失败不影响启动 */ }
  } catch (e) { /* 诊断失败不影响启动 */ }
  return results;
}

// `seed_db.js` 复用这里的逐条执行器（避免两处实现漂移）
module.exports = { bootstrap, execSqlFile };
