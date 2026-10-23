const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  const f = await L.waitOsReady(p, 20000);
  if (!f) return L.done(false, 'OS 未启动');
  await p.evaluate(() => { window.OS.openApp('files'); window.OS.openApp('settings'); });
  await p.waitForTimeout(1200);
  const before = await p.evaluate(() => window.OS.running().join(','));
  const r = await p.evaluate(() => window.OS.dev.exec('appreset', {}));
  await p.waitForTimeout(1200);
  const after = await p.evaluate(() => ({
    running: window.OS.running().slice(),
    apps: window.OS.apps().length,
    devterm: !!window.OS.apps().find(a => a.id === 'devterm')
  }));
  await b.close();
  const bad = [];
  if (after.apps !== 9) bad.push('appreset 后应用数=' + after.apps + '（期望 9）');
  if (!after.devterm) bad.push('devterm 被误删');
  if (after.running.includes('devterm')) bad.push('终端未被跳过');
  if (errs.length) bad.push('JS 错误: ' + errs[0]);
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : 'before=' + before + ' 返回=' + JSON.stringify(r.reset) ,
    { before, after: after.running });
})().catch(e => L.done(false, '异常: ' + e.message));
