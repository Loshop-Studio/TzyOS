const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 15000);
  // 写入损坏数据：非法 JSON、错误类型、超长串
  await p.evaluate(() => {
    localStorage.setItem('nova2.ui', '{{{ not json');
    localStorage.setItem('nova2.apps', '"should be array"');
    localStorage.setItem('nova2.vfs', 'null');
    localStorage.setItem('nova2.settings', '[]');
    localStorage.setItem('nova2.online', 'not-a-bool');
  });
  // 重新加载，OS 应能容错启动
  const p2 = await ctx.newPage();
  const errs2 = []; p2.on('pageerror', e => errs2.push(e.message));
  await p2.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  const f = await L.waitOsReady(p2, 20000);
  const st = f ? await f.evaluate(() => ({
    booted: !!window.OS.__booted, apps: window.OS.apps().length,
    icons: document.querySelectorAll('#nv-icons > *').length,
    desktop: !!document.getElementById('nv-desktop')
  })) : null;
  await L.shot(p2, 'T13_corrupt');
  await b.close();
  const bad = [];
  if (!f) bad.push('损坏存储后 OS 无法启动');
  if (st && st.desktop !== true) bad.push('桌面未渲染');
  if (st && st.apps < 8) bad.push('应用丢失 ' + st.apps);
  const allErr = errs.concat(errs2).filter(e => !/ResizeObserver/.test(e));
  if (allErr.length) bad.push('未捕获异常: ' + allErr.slice(0, 2).join(' | '));
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '存储损坏后仍正常启动 apps=' + (st && st.apps) + ' icons=' + (st && st.icons),
    { st });
})().catch(e => L.done(false, '异常: ' + e.message));
