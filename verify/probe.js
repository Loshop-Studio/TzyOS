const L = require('./lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await p.waitForTimeout(1800);
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(600);

  const probe = async (label, sel, props) => {
    const r = await p.evaluate(([s, ps]) => {
      const el = document.querySelector(s);
      if (!el) return null;
      const c = getComputedStyle(el);
      const o = {};
      ps.forEach(k => o[k] = c.getPropertyValue(k));
      return o;
    }, [sel, props]);
    console.log(label, JSON.stringify(r));
  };

  console.log('--- 桌面/状态栏 ---');
  await probe('body      ', 'body', ['background-color','color']);
  await probe('desktop   ', '#nv-desktop', ['background-color','background-image']);
  await probe('statusbar ', '.nv-statusbar', ['background-color','border-radius','color']);
  await probe('app icon  ', '.nv-app-icon', ['border-radius','background-image','background-color']);
  await probe('app name  ', '.nv-app-name', ['color']);

  await p.evaluate(() => window.OS.openApp('settings'));
  await p.waitForTimeout(1000);
  console.log('--- 窗口/设置 ---');
  await probe('winbox    ', '.winbox', ['background-color','border-radius']);
  await probe('wb-header ', '.winbox .wb-header', ['background-color','color']);
  await probe('wb-body   ', '.winbox .wb-body', ['background-color','color']);
  await probe('set card  ', '.nv-set-card', ['background-color','border-color','border-radius']);
  await probe('set h4    ', '.nv-set-card h4', ['color']);
  await probe('set row   ', '.nv-set-row', ['color','border-top-color']);
  await probe('nv-btn    ', '.nv-btn', ['background-color','color','border-color','border-radius']);
  console.log('--- 对比度自检（正文）---');
  const contrast = await p.evaluate(() => {
    const lum = c => { const v=[0,1,2].map(i=>{let x=parseInt(c[i],10)/255;return x<=0.03928?x/12.92:Math.pow((x+0.055)/1.055,2.4)});return 0.2126*v[0]+0.7152*v[1]+0.0722*v[2] };
    const parse = s => { const m=s.match(/\d+/g)||[0,0,0]; return m.slice(0,3) };
    const body = getComputedStyle(document.querySelector('.nv-set-card'));
    const fg = getComputedStyle(document.querySelector('.nv-set-row'));
    const l1 = lum(parse(body.backgroundColor)), l2 = lum(parse(fg.color));
    const hi = Math.max(l1,l2), lo = Math.min(l1,l2);
    return ((hi+0.05)/(lo+0.05)).toFixed(2);
  });
  console.log('设置页正文对比度:', contrast, '(>=4.5 为佳)');
  await b.close();
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
