const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  // 每个静态资源延迟 1.2s（低于看门狗 8s 阈值，但累积很慢）
  await p.route('**/__nova__/**/*.{js,css}', async r => {
    await new Promise(x => setTimeout(x, 1200));
    await r.continue();
  });
  const t0 = Date.now();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load', timeout: 60000 });
  const f = await L.waitOsReady(p, 40000);
  const dt = Date.now() - t0;
  const st = f ? await f.evaluate(() => ({ apps: window.OS.apps().length })) : null;
  await L.shot(p, 'T14_slow');
  await b.close();
  const bad = [];
  if (!f) bad.push('弱网下未能启动（' + dt + 'ms）');
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '弱网下 ' + dt + 'ms 启动成功 apps=' + (st && st.apps),
    { ms: dt });
})().catch(e => L.done(false, '异常: ' + e.message));
