const L = require('../lib');
const http = require('http'), fs = require('fs');
(async () => {
  const srv = await L.hostPageServer(8096);
  const b = await L.launch({ hostLocal: 8096 });
  const ctx = await b.newContext({ viewport: { width: 1280, height: 800 } });
  const p = await ctx.newPage();
  const nav = [];
  p.on('request', r => {
    const u = r.url();
    if (u.includes('nova.html') && !u.includes('host.local')) nav.push(u.split('/__nova__/')[1].slice(0, 40));
  });
  await p.goto('http://host.local:8096/column.html');
  await L.injectStub(p);
  // 前 2 次 os.js 坏，第 3 次恢复 -> 看门狗应自愈
  let n = 0;
  await p.route('**/os.js*', async r => {
    n++;
    if (n <= 2) await r.fulfill({ status: 200, contentType: 'application/javascript', body: 'BROKEN <<<' });
    else await r.continue();
  });
  await L.activate(p);
  const f = await L.waitOsReady(p, 40000);
  await L.shot(p, 'T11_heal');
  await b.close(); srv.close();
  const bad = [];
  if (!f) bad.push('看门狗未能自愈（os.js 请求 ' + n + ' 次，导航 ' + nav.length + ' 次）');
  L.done(bad.length === 0,
    bad.length ? bad.join('; ') : '自愈成功：os.js 请求 ' + n + ' 次，导航 ' + nav.length + ' 次',
    { nav, osjsRequests: n });
})().catch(e => L.done(false, '异常: ' + e.message));
