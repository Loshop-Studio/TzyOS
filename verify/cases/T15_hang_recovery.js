const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  const f = await L.waitOsReady(p, 20000);
  if (!f) return L.done(false, 'OS 未启动');

  // 模拟主线程卡死：同步阻塞 6 秒
  await p.evaluate(() => { const t = Date.now(); while (Date.now() - t < 6000) {} })
    .catch(() => {});
  await p.waitForTimeout(2500);
  // 卡死后仍应可交互：打开一个应用
  const ok = await p.evaluate(async () => {
    try {
      window.OS.openApp('files');
      await new Promise(r => setTimeout(r, 800));
      return window.OS.running().includes('files');
    } catch (e) { return false; }
  });
  // 再用维护终端重建，验证卡死后自恢复能力
  const r = await p.evaluate(() => window.OS.dev.exec('appreset', { id: 'files' }));
  await p.waitForTimeout(1000);
  const alive = await p.evaluate(() => !!window.OS && !!window.OS.__booted);
  await L.shot(p, 'T15_hang');
  await b.close();
  const bad = [];
  if (!ok) bad.push('卡死后无法打开应用');
  if (!alive) bad.push('卡死后 OS 不可用');
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '卡死 6s 后仍可交互，appreset 返回 ' + JSON.stringify(r.reset || r),
    { ok, alive });
})().catch(e => L.done(false, '异常: ' + e.message));
