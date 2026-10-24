const L = require('./lib');
const R = [];
const ok = (n,c,d) => R.push({n, ok:!!c, d:d||''});

(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = []; p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.waitForTimeout(500);

  // 预置密码，然后重新加载使锁屏进入“需要密码”态
  await p.evaluate(() => localStorage.setItem('nova2.pin', '"1234"'));
  await p.reload({ waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.waitForTimeout(800);

  const st = await p.evaluate(() => ({
    padHidden: document.getElementById('nv-pin-panel').classList.contains('nv-hidden'),
    hintHidden: document.getElementById('nv-enter-hint').classList.contains('nv-hidden'),
    setBtn: document.querySelector('.nv-key[data-k="set"]').textContent
  }));
  ok('有密码时显示键盘', !st.padHidden && st.hintHidden, JSON.stringify(st));
  ok('有密码时按钮为“改密码”', st.setBtn === '改密码', st.setBtn);

  const press = async (seq) => {
    await p.evaluate(async (ks) => {
      const keys = () => [...document.querySelectorAll('.nv-key')];
      for (const k of ks) {
        const btn = keys().find(x => x.getAttribute('data-k') === String(k));
        if (btn) btn.click();
        await new Promise(r => setTimeout(r, 60));
      }
      await new Promise(r => setTimeout(r, 450));
    }, seq);
  };
  const tip = () => p.evaluate(() => document.getElementById('nv-pin-tip').textContent);
  const locked = () => p.evaluate(() => !document.getElementById('nv-lock').classList.contains('nv-hidden'));

  // 错误密码
  await press(['9','9','9','9']);
  ok('错误密码保持锁定', await locked(), await tip());
  ok('错误提示正确', (await tip()).includes('错误') || (await tip()).includes('请输入'), await tip());

  // 正确密码
  await press(['1','2','3','4']);
  ok('正确密码解锁', !(await locked()), '');

  // 锁屏后用正确密码再进
  await p.evaluate(() => window.OS.lock());
  await p.waitForTimeout(600);
  ok('锁屏后回到密码态', await locked());
  await press(['1','2','3','4']);
  ok('再次解锁成功', !(await locked()), '');

  // 改密码：旧 1234 -> 新 5678
  await p.evaluate(() => window.OS.lock());
  await p.waitForTimeout(500);
  await p.evaluate(() => document.querySelector('.nv-key[data-k="set"]').click());
  await p.waitForTimeout(200);
  ok('改密码要求先验原密码', (await tip()).includes('原密码'), await tip());
  await press(['1','2','3','4']);       // 原密码
  await press(['5','6','7','8']);       // 新密码
  await press(['5','6','7','8']);       // 确认
  const saved = await p.evaluate(() => localStorage.getItem('nova2.pin'));
  ok('改密码已保存', saved === '"5678"', saved);

  // 取消：进设置态后按取消应回到验证态
  await p.evaluate(() => document.querySelector('.nv-key[data-k="set"]').click());
  await p.waitForTimeout(200);
  const cancelLabel = await p.evaluate(() => document.querySelector('.nv-key[data-k="set"]').textContent);
  ok('编辑态按钮为“取消”', cancelLabel === '取消', cancelLabel);
  await p.evaluate(() => document.querySelector('.nv-key[data-k="set"]').click());
  await p.waitForTimeout(300);
  ok('取消后回到验证态', (await tip()).includes('请输入锁屏密码'), await tip());

  await b.close();
  R.forEach(r => console.log((r.ok ? '  PASS ' : '  FAIL ') + r.n + (r.d ? '  ' + r.d : '')));
  if (errs.length) console.log('  JS 错误:', errs.slice(0,3));
  process.exit(R.some(r=>!r.ok) || errs.length ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
