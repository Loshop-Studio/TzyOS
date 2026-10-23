const L = require('../lib');
const fs = require('fs'), path = require('path');
(async () => {
  const dir = path.join(L.ROOT, 'novaos2/lib/phigros');
  const script = fs.readFileSync(path.join(dir, 'script-1.5.9.2.js'), 'utf8');
  const m = script.match(/interact\.js\?v=([0-9a-zA-Z-]+)/);
  const bad = [];
  if (!m) bad.push('script 中未找到 interact.js 版本号');
  else {
    const ver = m[1];
    const st = fs.statSync(path.join(dir, 'utils/interact.js'));
    const mt = String(Math.floor(st.mtimeMs / 1000));
    if (ver !== mt) bad.push('版本号 ' + ver + ' != interact.js mtime ' + mt + '（坏副本会再次被钉死）');
  }
  // 浏览器实际加载 phi，确认所有依赖 200
  const b = await L.launch();
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const bad200 = [];
  p.on('response', r => {
    const u = r.url();
    if (u.includes('/lib/phigros/') && r.status() >= 400) bad200.push(r.status() + ' ' + u.split('/lib/phigros/')[1]);
  });
  const errs = [];
  p.on('pageerror', e => errs.push(e.message));
  await p.goto(L.MOUNT + 'lib/phigros/index.html', { waitUntil: 'load', timeout: 30000 });
  await p.waitForTimeout(4000);
  const st = await p.evaluate(() => ({
    hasMain: !!document.querySelector('.main'),
    title: document.title,
    uploader: !!document.getElementById('uploader')
  }));
  await L.shot(p, 'T16_phi');
  await b.close();
  if (bad200.length) bad.push('依赖 4xx/5xx: ' + bad200.slice(0, 3).join(', '));
  if (errs.length) bad.push('JS 错误: ' + errs[0]);
  if (!st.hasMain) bad.push('phi 页面主体未渲染（白屏）');
  L.done(bad.length === 0, bad.length ? bad.join('; ') : '版本号与 mtime 一致，phi 渲染正常 title=' + st.title);
})().catch(e => L.done(false, '异常: ' + e.message));
