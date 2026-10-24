const L = require('./lib');
const R = []; const ok = (n,c,d)=>R.push({n,ok:!!c,d:d||''});
(async () => {
  const srv = await L.hostPageServer(8096);
  const b = await L.launch({ hostLocal: 8096 });
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto('http://host.local:8096/column.html');

  // 注入前不应有红点
  ok('注入前无红点', await p.evaluate(() => !document.getElementById('nv-inject-dot')));

  await L.injectStub(p);
  await p.waitForTimeout(800);

  const dot = await p.evaluate(() => {
    const d = document.getElementById('nv-inject-dot');
    if (!d) return null;
    const c = getComputedStyle(d);
    return { r: c.right, b: c.bottom, w: c.width, h: c.height,
             bg: c.backgroundColor, z: c.zIndex, pe: c.pointerEvents,
             rect: d.getBoundingClientRect().toJSON() };
  });
  ok('注入后出现红点', !!dot, JSON.stringify(dot && {r:dot.r,b:dot.b,w:dot.w,h:dot.h}));
  if (dot) {
    ok('位置在右下角', parseFloat(dot.r) <= 6 && parseFloat(dot.b) <= 6, dot.r + '/' + dot.b);
    ok('尺寸极小(<=8px)', parseFloat(dot.w) <= 8 && parseFloat(dot.h) <= 8, dot.w + 'x' + dot.h);
    ok('红色', /226,\s*59,\s*46|e2/i.test(dot.bg) || dot.bg.includes('rgb'), dot.bg);
    ok('不拦截点击', dot.pe === 'none', dot.pe);
    // 实际落在视口右下
    ok('实际坐标贴右下', dot.rect.right >= 1270 && dot.rect.bottom >= 790,
       JSON.stringify({r:dot.rect.right, b:dot.rect.bottom}));
  }

  // 激活后消失
  await L.activate(p);
  await p.waitForTimeout(2500);
  ok('激活后红点消失', await p.evaluate(() => !document.getElementById('nv-inject-dot')));

  // 再次加载（已激活）不应重现
  await p.reload({ waitUntil: 'load' });
  await L.injectStub(p);
  await p.waitForTimeout(800);
  ok('已激活后不再显示', await p.evaluate(() => !document.getElementById('nv-inject-dot')));

  await b.close(); srv.close();
  R.forEach(r => console.log((r.ok?'  PASS ':'  FAIL ')+r.n+(r.d?'  '+r.d:'')));
  if (errs.length) console.log('  JS 错误:', errs.slice(0,3));
  process.exit(R.some(r=>!r.ok)||errs.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
