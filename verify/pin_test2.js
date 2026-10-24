const L = require('./lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.evaluate(() => localStorage.setItem('nova2.pin', '"1234"'));
  await p.reload({ waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.waitForTimeout(700);

  const press = async (seq, gap) => {
    await p.evaluate(async ([ks, g]) => {
      const keys = () => [...document.querySelectorAll('.nv-key')];
      for (const k of ks) {
        const btn = keys().find(x => x.getAttribute('data-k') === String(k));
        if (btn) btn.click();
        await new Promise(r => setTimeout(r, g));
      }
      await new Promise(r => setTimeout(r, 500));
    }, [seq, gap||60]);
  };
  const lockedState = () => p.evaluate(() => ({
    locked: !document.getElementById('nv-lock').classList.contains('nv-hidden'),
    tip: document.getElementById('nv-pin-tip').textContent,
    dots: document.getElementById('nv-pin-dots').textContent
  }));

  // 输错
  await press(['9','9','9','9']);
  console.log('输错后:', JSON.stringify(await lockedState()));
  // 等待冷却结束（700ms 错误展示）
  await p.waitForTimeout(900);
  console.log('冷却后:', JSON.stringify(await lockedState()));
  // 立即输正确
  await press(['1','2','3','4']);
  const r = await lockedState();
  console.log('输入正确后:', JSON.stringify(r));
  console.log(r.locked ? '  FAIL 仍未解锁' : '  PASS 已解锁');
  await b.close();
  process.exit(r.locked ? 1 : 0);
})().catch(e => { console.error('FATAL', e.message); process.exit(1); });
