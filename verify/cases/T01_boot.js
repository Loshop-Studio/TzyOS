const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  p.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()); });

  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load', timeout: 30000 });
  await L.waitOsReady(p, 20000);
  const st = await p.evaluate(() => ({
    booted: !!(window.OS && window.OS.__booted),
    apps: window.OS && window.OS.apps ? window.OS.apps().map(a => a.id) : [],
    icons: document.querySelectorAll('#nv-icons > *').length,
    dock: !!document.getElementById('nv-dock')
  }));
  await L.shot(p, 'T01_boot');
  await b.close();

  const problems = [];
  if (!st.booted) problems.push('OS 未 boot');
  if (st.apps.length !== 9) problems.push('应用数=' + st.apps.length + '（期望 9）');
  if (st.icons < 6) problems.push('桌面图标=' + st.icons);
  if (!st.dock) problems.push('缺少 dock');
  if (errs.length) problems.push('JS 错误: ' + errs.slice(0, 3).join(' | '));
  L.done(problems.length === 0,
    problems.length ? problems.join('; ') : 'booted, apps=' + st.apps.length + ', icons=' + st.icons,
    { apps: st.apps });
})().catch(e => L.done(false, '异常: ' + e.message));
