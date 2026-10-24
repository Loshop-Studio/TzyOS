const L = require('./lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await p.waitForTimeout(1600);
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(500);
  await p.evaluate(() => window.OS.openApp('settings'));
  await p.waitForTimeout(1000);
  const r = await p.evaluate(() => {
    const out = [];
    document.querySelectorAll('.nv-set-row').forEach((el, i) => {
      if (i > 3) return;
      const c = getComputedStyle(el);
      out.push({ i, border: c.borderTopWidth + ' ' + c.borderTopColor, color: c.color });
    });
    const btns = [...document.querySelectorAll('.nv-btn')].slice(0, 4).map(el => {
      const c = getComputedStyle(el);
      return { t: el.textContent.trim().slice(0,6), bg: c.backgroundColor, fg: c.color, cls: el.className };
    });
    return { rows: out, btns };
  });
  console.log(JSON.stringify(r, null, 1));
  await b.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
