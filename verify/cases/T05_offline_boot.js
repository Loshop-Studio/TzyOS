const L = require('../lib');
const { execSync } = require('child_process');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  const warm = await L.waitOsReady(p, 20000);
  if (!warm) return L.done(false, '预热阶段 OS 未启动');

  // 停后端（保持网络），模拟断劫持
  try { execSync('pkill -f verify/server.py'); } catch (e) {}
  await p.waitForTimeout(1500);

  // 真实用户行为：新开一个页面（非 reload，reload 会强制绕缓存）
  const p2 = await ctx.newPage();
  const errs = [];
  p2.on('pageerror', e => errs.push(e.message));
  let navOk = true;
  try { await p2.goto(L.MOUNT + 'nova.html', { waitUntil: 'load', timeout: 20000 }); }
  catch (e) { navOk = false; }
  const f = await L.waitOsReady(p2, 15000);
  const st = f ? await f.evaluate(() => ({
    apps: window.OS.apps().length,
    icons: document.querySelectorAll('#nv-icons > *').length,
    online: window.OS.net.online()
  })) : null;
  await L.shot(p2, 'T05_offline_boot');
  await b.close();
  const bad = [];
  if (!navOk) bad.push('断网后页面导航失败');
  if (!f) bad.push('断网后 OS 未从缓存启动');
  if (st && st.online !== false) bad.push('离线态判断错误 online=' + st.online);
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '断网后仍启动 apps=' + (st && st.apps) + ' online=' + (st && st.online),
    { st });
})().catch(e => L.done(false, '异常: ' + e.message));
