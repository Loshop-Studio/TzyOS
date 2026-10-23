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
  await icon.asElement().click();
  await p.waitForTimeout(1200);

  const run = async (cmd) => {
    const n = await p.evaluate(() => document.querySelectorAll('.nv-term-out .nv-term-ln').length);
    await p.fill('.nv-term-input', cmd);
    await p.press('.nv-term-input', 'Enter');
    await p.waitForTimeout(600);
    return p.evaluate(n0 => [...document.querySelectorAll('.nv-term-out .nv-term-ln')]
      .slice(n0).map(e => e.textContent).join(' ').replace(/\s+/g, ' '), n);
  };
  const bad = [];
  const expect = async (cmd, re, label) => {
    const out = await run(cmd);
    if (!re.test(out)) bad.push((label || cmd) + ' 输出异常: ' + out.slice(0, 80));
    return out;
  };
  await expect('ping', /"pong"\s*:\s*true/);
  await expect('info', /"ua"|"net"/);
  await expect('cache keys', /"count"/);
  await expect('appreset list', /"apps"/);
  await expect('dump', /"localStorage"/);
  await expect('help', /诊断/);
  await expect('cache purge', /确定继续|confirm/);
  await expect('sysreset wipe', /确定继续|confirm/);
  await expect('hostpage bogus', /未知 act/);
  await expect('nosuchcmd', /未知命令/);
  await L.shot(p, 'T04_devterm_cmds');
  await b.close();
  if (errs.length) bad.push('JS 错误: ' + errs[0]);
  L.done(bad.length === 0, bad.length ? bad.join('; ') : '10 条命令全部符合预期');
})().catch(e => L.done(false, '异常: ' + e.message));
