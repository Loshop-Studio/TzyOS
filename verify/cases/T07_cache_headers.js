const L = require('../lib');
const http = require('http');
function head(p) {
  return new Promise((res, rej) => {
    const r = http.request({ host: '127.0.0.1', port: L.PORT, path: '/__nova__/' + p,
      method: 'GET', headers: { Host: L.HOST } }, resp => {
      let n = 0;
      resp.on('data', c => { n += c.length; });
      resp.on('end', () => res({ status: resp.statusCode, cc: resp.headers['cache-control'] || '',
        etag: resp.headers.etag || '', bytes: n }));
    });
    r.on('error', rej); r.end();
  });
}
(async () => {
  const checks = [
    ['nova.html', /max-age=60/, false],
    ['lib/phigros/index.html', /max-age=60/, true],
    ['lib/mojuan/index.html', /max-age=60/, true],
    ['__boot__.snapshot.js', /max-age=31536000/, false],
    ['lib/phigros/script-1.5.9.2.js', /max-age=86400/, true],
    ['os.js', /max-age=31536000/, false]
  ];
  const bad = [], rows = [];
  for (const [p, re, wantEtag] of checks) {
    const r = await head(p);
    rows.push(p + ' ' + r.cc + (r.etag ? ' +ETag' : ''));
    if (r.status !== 200) { bad.push(p + ' 状态 ' + r.status); continue; }
    if (!re.test(r.cc)) bad.push(p + ' Cache-Control 不符: ' + r.cc);
    if (wantEtag && !r.etag) bad.push(p + ' 缺少 ETag');
  }
  L.done(bad.length === 0, bad.length ? bad.join('; ') : rows.join(' | '), { rows });
})().catch(e => L.done(false, '异常: ' + e.message));
