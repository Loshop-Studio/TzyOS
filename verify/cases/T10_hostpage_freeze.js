const L = require('../lib');
(async () => {
  const srv = await L.hostPageServer(8096);
  const b = await L.launch({ hostLocal: 8096 });
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto('http://host.local:8096/column.html');
  await L.injectStub(p);
  await L.activate(p);
  const f = await L.waitOsReady(p, 25000);
  if (!f) return L.done(false, 'OS 未启动');

  const before = await p.evaluate(() => ({ cls: document.documentElement.className,
    ov: document.documentElement.style.overflow }));
  await f.evaluate(() => window.OS.dev.exec('hostpage', { act: 'freeze' }));
  await p.waitForTimeout(700);
  const mid = await p.evaluate(() => ({
    cls: document.documentElement.className,
    ov: document.documentElement.style.overflow,
    style: !!document.querySelector('style[data-nova-freeze]')
  }));
  await L.shot(p, 'T10_frozen');
  await f.evaluate(() => window.OS.dev.exec('hostpage', { act: 'thaw' }));
  await p.waitForTimeout(700);
  const after = await p.evaluate(() => ({ cls: document.documentElement.className,
    ov: document.documentElement.style.overflow }));
  await b.close(); srv.close();

  const bad = [];
  if (mid.cls.indexOf('nv-host-frozen') < 0) bad.push('未加 nv-host-frozen');
  if (mid.ov !== 'hidden') bad.push('滚动未锁定 ov=' + mid.ov);
  if (!mid.style) bad.push('未注入冻结样式');
  if (after.cls.indexOf('nv-host-frozen') >= 0) bad.push('thaw 未移除类名');
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '冻结/解冻正确 before=' + JSON.stringify(before) + ' after=' + JSON.stringify(after));
})().catch(e => L.done(false, '异常: ' + e.message));
