/**
 * 由管线提供（确定性，零 LLM）：**认证的唯一入口**。
 *
 * 为什么必须由管线给：种子数据是管线写的（从测试夹具抽出的邮箱/口令），
 * 而登录校验是模型写的 —— 两边**各写一套哈希方式就一定对不上**，
 * 于是"夹具里明明有账号，登录却永远失败"，而判据几乎每条都要先登录。
 * 所以这里定死一套：注册时用 hashPassword，登录时用 verifyPassword，
 * 并且**兼容明文**（种子里的口令是明文），这样种子账号一定能登录。
 */
const crypto = require('crypto');

const SECRET = process.env.SESSION_SECRET || 'arcbench-dev-secret';
const COOKIE = 'session_id';

function hashPassword(plain) {
  return 'sha256:' + crypto.createHash('sha256').update(String(plain)).digest('hex');
}

/** 校验：支持管线哈希、sha256 十六进制、以及**明文**（种子数据是明文）。 */
function verifyPassword(user, plain) {
  if (!user) return false;
  const stored = user.password_hash || user.password || user.passwordHash || '';
  const given = String(plain == null ? '' : plain);
  if (!stored) return false;
  if (stored === given) return true;                       // 明文（种子）
  if (stored === hashPassword(given)) return true;         // 本模块的哈希
  if (/^[a-f0-9]{64}$/i.test(stored)) {                    // 裸 sha256
    return stored.toLowerCase() === crypto.createHash('sha256').update(given).digest('hex');
  }
  return false;
}

function newSessionId() {
  return crypto.randomBytes(24).toString('hex');
}

/** 解析请求里的会话（cookie 里的 session_id → 用户行）。 */
async function currentUser(req, db) {
  const sid = parseCookie(req.headers.cookie || '')[COOKIE];
  if (!sid) return null;
  const { get } = db;
  const rows = await get('SELECT user_id FROM sessions WHERE session_id = ?', [sid]);
  if (!rows) return null;
  const uid = rows.user_id;
  return await get('SELECT * FROM users WHERE id = ?', [uid]);
}


/** 常用中间件：模型很自然会写 `auth.requireAuth` —— 提供它，避免"处理函数是 undefined"。 */
async function requireAuth(req, res, next) {
  try {
    const user = await currentUser(req, { get: require('../database').get });
    if (!user) return res.status(401).json({ error: 'Authentication required' });
    req.user = user;
    return next();
  } catch (error) {
    return res.status(401).json({ error: 'Authentication required' });
  }
}

async function optionalAuth(req, res, next) {
  try {
    req.user = await currentUser(req, { get: require('../database').get });
  } catch (error) {
    req.user = null;
  }
  return next();
}

function requireRole() {
  return function roleGuard(req, res, next) { return next(); };
}

function parseCookie(header) {
  const out = {};
  for (const part of String(header).split(';')) {
    const i = part.indexOf('=');
    if (i > 0) out[part.slice(0, i).trim()] = decodeURIComponent(part.slice(i + 1).trim());
  }
  return out;
}

function setSessionCookie(res, sid, maxAgeSeconds = 86400) {
  res.setHeader('Set-Cookie',
    COOKIE + '=' + encodeURIComponent(sid) + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + maxAgeSeconds);
}

function clearSessionCookie(res) {
  res.setHeader('Set-Cookie', COOKIE + '=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0');
}

module.exports = { hashPassword, verifyPassword, newSessionId, currentUser, setSessionCookie,
  clearSessionCookie, parseCookie, requireAuth, optionalAuth, requireRole, COOKIE, SECRET };
