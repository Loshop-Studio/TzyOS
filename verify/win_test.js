const L = require('./lib');
const R = []; const ok=(n,c,d)=>R.push({n,ok:!!c,d:d||''});
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs=[]; p.on('pageerror', e=>errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(600);

  // 打开文件管理器
  await p.evaluate(() => window.OS.openApp('files'));
  await p.waitForTimeout(1200);
  const has = await p.evaluate(() => ({
    win: !!(window.OS.running()||[]).length,
    header: !!document.querySelector('.winbox .wb-header'),
    dbl: document.querySelector('.winbox .wb-header') ?
         document.querySelector('.winbox .wb-header').getAttribute('data-dblclick') : null
  }));
  ok('窗口已打开', has.win && has.header, JSON.stringify(has));
  ok('标题栏已绑定双击', has.dbl === '1', String(has.dbl));

  // 双击标题栏 -> 最大化
  const before = await p.evaluate(() => {
    const el = document.querySelector('.winbox');
    return { w: el.getBoundingClientRect().width, max: el.classList.contains('max') };
  });
  await p.dblclick('.winbox .wb-header');
  await p.waitForTimeout(700);
  const after = await p.evaluate(() => {
    const el = document.querySelector('.winbox');
    return { w: el.getBoundingClientRect().width, max: el.classList.contains('max') };
  });
  ok('双击标题栏最大化', after.max || after.w > before.w, JSON.stringify({before, after}));

  // 状态栏按钮
  const btns = await p.evaluate(() => ({
    home: document.getElementById('nv-sb-home').textContent.trim(),
    task: document.getElementById('nv-sb-task').textContent.trim(),
    lock: document.getElementById('nv-sb-lock').textContent.trim(),
    back: document.getElementById('nv-sb-back').textContent.trim()
  }));
  ok('状态栏按钮文案清晰', btns.home==='回桌面'&&btns.task==='窗口'&&btns.lock==='锁屏'&&btns.back==='返回', JSON.stringify(btns));

  // 点“窗口”打开任务视图
  await p.click('#nv-sb-task');
  await p.waitForTimeout(700);
  ok('任务视图可打开', await p.evaluate(() => !!document.querySelector('.nv-taskview')));
  await p.keyboard.press('Escape').catch(()=>{});
  await p.waitForTimeout(400);

  // 点“回桌面”最小化全部
  await p.click('#nv-sb-home');
  await p.waitForTimeout(700);
  const min = await p.evaluate(() => {
    const ws = [...document.querySelectorAll('.winbox')];
    return ws.length ? ws.every(w => w.classList.contains('min')) : true;
  });
  ok('回桌面最小化全部窗口', min);

  await b.close();
  R.forEach(r=>console.log((r.ok?'  PASS ':'  FAIL ')+r.n+(r.d?'  '+r.d:'')));
  if (errs.length) console.log('  JS 错误:', errs.slice(0,3));
  process.exit(R.some(r=>!r.ok)||errs.length?1:0);
})().catch(e=>{console.error('FATAL',e.message);process.exit(1);});
