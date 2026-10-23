const L = require('../lib');
(async () => {
  const srv = await L.hostPageServer(8096);
  const b = await L.launch({ hostLocal: 8096 });
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto('http://host.local:8096/column.html');
  await L.injectStub(p);
  const stealth = await p.evaluate(() => typeof window.__novaStealth);
  await L.activate(p);
  const osFrame = await L.waitOsReady(p, 25000);
  const info = osFrame ? await osFrame.evaluate(() => ({
    apps: window.OS.apps().length,
    icons: document.querySelectorAll('#nv-icons > *').length
  })) : null;
  await L.shot(p, 'T02_inject');
  await b.close(); srv.close();

  const bad = [];
  if (stealth !== 'boolean') bad.push('stub 未执行');
  if (!osFrame) bad.push('注入后 OS 未 boot');
  if (info && info.apps !== 9) bad.push('应用数=' + info.apps);
  if (errs.length) bad.push('JS 错误: ' + errs.slice(0, 2).join(' | '));
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '注入链完整，apps=' + (info && info.apps) + ', icons=' + (info && info.icons));
})().catch(e => L.done(false, '异常: ' + e.message));
