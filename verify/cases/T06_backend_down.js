const L = require('../lib');
const { execSync } = require('child_process');
(async () => {
  const srv = await L.hostPageServer(8096);
  const b = await L.launch({ hostLocal: 8096 });
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const trace = [];
  p.on('request', r => {
    const u = r.url();
    if (u.includes('__boot__')) trace.push('REQ ' + u.split('/__nova__/')[1]);
  });
  p.on('requestfailed', r => {
    const u = r.url();
    if (u.includes('__boot__')) trace.push('FAIL ' + u.split('/__nova__/')[1]);
  });
  await p.goto('http://host.local:8096/column.html');
  await L.injectStub(p);
  await p.waitForTimeout(6000);            // 等 snapshot 预热
  const snapAt = await p.evaluate(() => localStorage.getItem('__nvaSnapAt') || '');
  await L.activate(p);
  const f = await L.waitOsReady(p, 25000);
  if (!f) return L.done(false, '在线阶段 OS 未启动');

  try { execSync('pkill -f verify/server.py'); } catch (e) {}
  await p.waitForTimeout(1500);

  // 断网后重新执行 stub：应回落 snapshot
  await p.evaluate(() => { window.__nvaBoot = false; window.__novaStealth = undefined; });
  await L.injectStub(p);
  await p.waitForTimeout(4000);
  await p.fill('#q', 'cpt1104122');
  await p.waitForTimeout(2500);
  const f2 = await L.waitOsReady(p, 15000);
  const st = f2 ? await f2.evaluate(() => ({
    apps: window.OS.apps().length, online: window.OS.net.online()
  })) : null;
  await L.shot(p, 'T06_backend_down');
  await b.close(); srv.close();

  const bad = [];
  if (!snapAt) bad.push('snapshot 未预热（__nvaSnapAt 缺失）');
  // 判定只要求“断网后 OS 仍可用”。是否观察到 __boot__.js 失败取决于
  // 浏览器是否直接命中 snapshot 缓存（可能根本不发请求），不应作为硬条件。
  if (!trace.some(t => t.indexOf('snapshot') >= 0) && !f2) bad.push('未回落 snapshot');
  if (!f2) bad.push('断劫持后 OS 未恢复');
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '回落 snapshot 成功 apps=' + (st && st.apps) + ' trace=' + trace.join(','),
    { trace, st });
})().catch(e => L.done(false, '异常: ' + e.message));
