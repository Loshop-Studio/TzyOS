const L = require('./lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await p.waitForTimeout(2000);
  await L.shot(p, 'design_lock');
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(800);
  await L.shot(p, 'design_desktop');
  // 打开设置
  await p.evaluate(() => window.OS.openApp('settings'));
  await p.waitForTimeout(1200);
  await L.shot(p, 'design_settings');
  console.log('errors:', errs.length ? errs.slice(0,3) : 'none');
  await b.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
