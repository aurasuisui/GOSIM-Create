/**
 * 由管线提供（确定性，零 LLM）：**会话端点的唯一实现**。
 *
 * 为什么由管线给（实测三轮的结论）：模型写的页面会在挂载时调 `/api/auth/me`、`/api/auth/check`、
 * `/api/auth/logout`，而它自己写的 auth 路由**只实现了 `/login`** → 这些调用 404 →
 * 页面判定"未登录" → 已登录的导航与内容**永不渲染** → 判据几乎全挂。
 * 端点命名有多种习惯（`/api/login` vs `/api/auth/login`），所以本路由会被挂到
 * **`/api` 与 `/api/auth` 两处**，四种命名都能命中。
 */
const express = require('express');
const auth = require('../auth');
const { get, run } = require('../database');

const router = express.Router();

function publicUser(user) {
  if (!user) return null;
  const username = user.username || user.nickname || user.name || '';
  return {
    id: user.id,
    username,
    nickname: user.nickname || username,
    name: user.name || username,
    email: user.email,
  };
}

/** 登录：**用管线那套校验**（种子里的口令是明文，模型自己写的哈希对不上）。 */
async function login(req, res) {
  try {
    const body = req.body || {};
    const email = body.email || body.username || body.nickname;
    const password = body.password;
    if (!email || !password) return res.status(400).json({ error: 'Email and password are required' });
    const user = await get('SELECT * FROM users WHERE email = ? OR username = ? OR nickname = ?',
                           [email, email, email]);
    if (!user || !auth.verifyPassword(user, password)) {
      return res.status(401).json({ error: 'Invalid email or password' });
    }
    try {
      await run('DELETE FROM sessions WHERE user_id = ?', [user.id]);
    } catch (e) { /* sessions 表可能不存在 */ }
    const sid = auth.newSessionId();
    try {
      await run('INSERT INTO sessions (session_id, user_id) VALUES (?, ?)', [sid, user.id]);
    } catch (e) { /* 没有 sessions 表就退化成"只有 cookie" */ }
    auth.setSessionCookie(res, sid);
    const pub = publicUser(user);
    // 🔴 响应形状要**最大化兼容**：实测模型写的登录页判的是 `res.data.success`，
    // 而我们的登录返回的是用户对象 → 页面拿到 200 却渲染 "Invalid credentials"，
    // 于是"接口通了但界面永远登不进"。这几个标志位是廉价且无害的兼容层。
    return res.status(200).json({ success: true, ok: true, authenticated: true, ...pub, user: pub });
  } catch (err) {
    console.error('[session] login failed:', err);
    return res.status(500).json({ error: 'Internal server error' });
  }
}

async function logout(req, res) {
  try {
    const sid = (auth.parseCookie ? auth.parseCookie(req.headers.cookie || '') : {})[auth.COOKIE];
    if (sid) { try { await run('DELETE FROM sessions WHERE session_id = ?', [sid]); } catch (e) {} }
  } catch (e) { /* ignore */ }
  auth.clearSessionCookie(res);
  return res.status(200).json({ ok: true });
}

async function me(req, res) {
  try {
    const user = await auth.currentUser(req, { get });
    if (!user) return res.status(401).json({ authenticated: false });
    const pub = publicUser(user);
    return res.status(200).json({ success: true, ok: true, authenticated: true, ...pub, user: pub });
  } catch (err) {
    return res.status(401).json({ authenticated: false });
  }
}

async function check(req, res) {
  try {
    const user = await auth.currentUser(req, { get });
    return res.status(200).json({ authenticated: Boolean(user), user: publicUser(user) });
  } catch (err) {
    return res.status(200).json({ authenticated: false, user: null });
  }
}

router.post('/login', login);
router.post('/signin', login);
router.post('/logout', logout);
router.post('/signout', logout);
router.get('/me', me);
router.get('/session', me);
router.get('/check', check);
router.get('/status', check);      // 模型常自己发明 /status：给它，别让页面 404
router.get('/current', me);

module.exports = router;
