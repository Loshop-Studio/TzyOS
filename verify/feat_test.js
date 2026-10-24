const L = require('./lib');
const R = [];
function ok(name, cond, detail) { R.push({name, ok: !!cond, detail: detail||''}); }

(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);

  // 1. 默认壁纸（预设）
  const wp = await p.evaluate(() => {
    const d = document.getElementById('nv-desktop');
    return { cls: d.className, a: getComputedStyle(d).getPropertyValue('--wp-a').trim() };
  });
  ok('预设壁纸生效', /nv-wp-\d/.test(wp.cls), JSON.stringify(wp));

  // 2. 解锁
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(600);

  // 3. 设置页版本号
  await p.evaluate(() => window.OS.openApp('settings'));
  await p.waitForTimeout(1200);
  const ver = await p.evaluate(() => {
    const rows = [...document.querySelectorAll('.nv-set-row')];
    const r = rows.find(x => x.textContent.includes('系统版本'));
    return r ? r.textContent.replace(/\s+/g,' ').trim() : null;
  });
  ok('设置页显示版本号', ver && /v\d+\.\d+\.\d+/.test(ver), ver);

  // 4. 自定义渐变壁纸
  const grad = await p.evaluate(() => {
    window.OS.ui.setWallpaper(window.OS.ui.customId(), ['#123456', '#abcdef']);
    const d = document.getElementById('nv-desktop'), l = document.getElementById('nv-lock');
    return {
      dcls: d.className, a: getComputedStyle(d).getPropertyValue('--wp-a').trim(),
      b: getComputedStyle(d).getPropertyValue('--wp-b').trim(),
      lcls: l.className
    };
  });
  ok('自定义渐变生效', grad.dcls.includes('nv-wp-custom') && grad.a === '#123456' && grad.b === '#abcdef', JSON.stringify(grad));
  ok('锁屏同步渐变', grad.lcls.includes('nv-wp-custom'));

  // 5. 持久化：重载后仍是自定义
  await p.reload({ waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  const persisted = await p.evaluate(() => {
    const d = document.getElementById('nv-desktop');
    return { cls: d.className, a: getComputedStyle(d).getPropertyValue('--wp-a').trim() };
  });
  ok('自定义壁纸持久化', persisted.cls.includes('nv-wp-custom') && persisted.a === '#123456', JSON.stringify(persisted));

  // 6. 锁屏密码：设置 -> 错误 -> 正确
  const pinFlow = await p.evaluate(async () => {
    const out = {};
    const lock = document.getElementById('nv-lock');
    const keys = () => [...document.querySelectorAll('.nv-key')];
    const press = k => { const b = keys().find(x => x.getAttribute('data-k') === String(k)); b && b.click(); };
    const dots = () => document.getElementById('nv-pin-dots').textContent;
    const tip = () => document.getElementById('nv-pin-tip').textContent;

    // 进入设置密码流程
    press('set');
    out.tipAfterSet = tip();
    // 输入新密码 1234
    ['1','2','3','4'].forEach(press);
    await new Promise(r => setTimeout(r, 300));
    out.tipAfterNew = tip();
    ['1','2','3','4'].forEach(press);
    await new Promise(r => setTimeout(r, 300));
    out.tipAfterConfirm = tip();
    out.saved = localStorage.getItem('nova2.pin');
    return out;
  });
  ok('设密码流程完成', pinFlow.saved === '"1234"' || pinFlow.saved === '1234', JSON.stringify(pinFlow));

  await b.close();
  const bad = R.filter(r => !r.ok);
  R.forEach(r => console.log((r.ok ? '  PASS ' : '  FAIL ') + r.name + (r.detail ? '  ' + r.detail : '')));
  if (errs.length) console.log('  JS 错误:', errs.slice(0,3));
  process.exit(bad.length || errs.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
