const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  // 让带 ?v= 的旧 URL 返回 404，模拟版本号指向已不存在的文件
  let hit = 0;
  await p.route('**/apps/devterm.js?v=*', async r => {
    hit++;
    await r.fulfill({ status: 404, body: 'gone' });
  });
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  const f = await L.waitOsReady(p, 20000);
  const st = f ? await f.evaluate(() => ({
    apps: window.OS.apps().length, booted: !!window.OS.__booted
  })) : null;
  await L.shot(p, 'T12_hash');
  await b.close();
  const bad = [];
  if (!f) bad.push('单文件 404 导致 OS 无法启动');
  if (st && st.apps < 8) bad.push('应用数骤降 ' + st.apps);
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '单个应用 404 不影响 OS（拦截 ' + hit + ' 次，apps=' + (st && st.apps) + '）',
    { hit, st });
})().catch(e => L.done(false, '异常: ' + e.message));
