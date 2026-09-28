const sqlite3 = require('sqlite3');
const db = new sqlite3.Database(process.argv[2]);
db.all("select name from sqlite_master where type='table'", (e, rows) => {
  if (e) { console.error('ERR', e.message); process.exit(1); }
  console.log('tables:', rows.map(r => r.name).join(', '));
  const names = rows.map(r => r.name).filter(n => !n.startsWith('sqlite_'));
  let i = 0;
  const next = () => {
    if (i >= names.length) return db.close();
    const n = names[i++];
    db.all('select count(*) as c from ' + n, (e2, r2) => {
      console.log('  ' + n + ': ' + (e2 ? 'ERR ' + e2.message : r2[0].c + ' rows'));
      next();
    });
  };
  next();
});