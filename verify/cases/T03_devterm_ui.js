const L = require('../lib');
(async () => {
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'nova.html', { waitUntil: 'load' });
  await L.waitOsReady(p, 20000);
  await p.click('#nv-lock', { force: true });
  await p.waitForTimeout(700);
  const icon = await p.evaluateHandle(() =>
    [...document.querySelectorAll('#nv-icons > *')].find(e => e.textContent.includes('⌨')));
  if (!icon.asElement()) return L.done(false, '桌面找不到维护终端图标');
  await icon.asElement().click();
  await p.waitForTimeout(1500);
  const ui = await p.evaluate(() => ({
    quick: !!document.querySelector('.nv-term-quick'),
    btns: document.querySelectorAll('.nv-term-quick .nv-term-q').length,
    input: !!document.querySelector('.nv-term-input'),
    lines: document.querySelectorAll('.nv-term-out .nv-term-ln').length
  }));
  await L.shot(p, 'T03_devterm_ui');
  await b.close();
  const bad = [];
  if (!ui.quick) bad.push('缺少快捷条');
  if (ui.btns !== 10) bad.push('快捷按钮=' + ui.btns + '（期望 10）');
  if (!ui.input) bad.push('缺少输入框');
  if (ui.lines < 1) bad.push('横幅未输出');
  if (errs.length) bad.push('JS 错误: ' + errs[0]);
  L.done(bad.length === 0, bad.length ? bad.join('; ') : JSON.stringify(ui));
})().catch(e => L.done(false, '异常: ' + e.message));
