const L = require('./lib');
const R=[]; const ok=(n,c,d)=>R.push({n,ok:!!c,d:d||''});
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs=[]; p.on('pageerror',e=>errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.waitForTimeout(800);

  // 锁屏无残留署名
  const foot = await p.evaluate(() => (document.querySelector('.nv-lock-foot')||{}).textContent||'');
  ok('锁屏无署名', !/Loshop|Cpt/i.test(foot), JSON.stringify(foot.trim()));

  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(600);

  // 桌面深色
  const bg = await p.evaluate(() => getComputedStyle(document.body).backgroundColor);
  ok('深色底', /rgb\(2\d, 2\d, \d+\)/.test(bg), bg);

  // 设置页
  await p.evaluate(() => window.OS.openApp('settings'));
  await p.waitForTimeout(1000);
  const set = await p.evaluate(() => {
    const t = document.querySelector('.nv-set') ? document.querySelector('.nv-set').textContent : '';
    return { hasVer: /v\d+\.\d+\.\d+/.test(t), hasAuthor: /Loshop|Cpt|作者/.test(t),
             bg: getComputedStyle(document.querySelector('.nv-set-card')).backgroundColor };
  });
  ok('设置页显示版本号', set.hasVer);
  ok('设置页无作者信息', !set.hasAuthor, JSON.stringify(set.hasAuthor));
  ok('设置卡片深色', /rgb\(3\d, 3\d, 4\d\)/.test(set.bg), set.bg);

  // 全部应用可打开
  const apps = await p.evaluate(() => window.OS.apps().map(a=>a.id));
  let opened = [];
  for (const id of apps) {
    const r = await p.evaluate((i) => { try { window.OS.openApp(i); return true; } catch(e){ return false; } }, id);
    if (r) opened.push(id);
  }
  await p.waitForTimeout(1500);
  ok('全部应用可打开', opened.length === apps.length, opened.length + '/' + apps.length);
  ok('无 JS 错误', errs.length === 0, errs.slice(0,2).join(' | '));

  await b.close();
  R.forEach(r=>console.log((r.ok?'  PASS ':'  FAIL ')+r.n+(r.d?'  '+r.d:'')));
  process.exit(R.some(r=>!r.ok)?1:0);
})().catch(e=>{console.error('FATAL',e.message);process.exit(1);});
