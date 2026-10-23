const L = require('../lib');
const http = require('http');
function req(p, extra) {
  return new Promise((res, rej) => {
    const r = http.request({ host: '127.0.0.1', port: L.PORT, path: '/__nova__/' + p,
      headers: Object.assign({ Host: L.HOST }, extra || {}) }, resp => {
      let n = 0; resp.on('data', c => { n += c.length; });
      resp.on('end', () => res({ status: resp.statusCode, etag: resp.headers.etag || '', bytes: n }));
    });
    r.on('error', rej); r.end();
  });
}
(async () => {
  const p = 'lib/phigros/script-1.5.9.2.js';
  const a = await req(p);
  if (!a.etag) return L.done(false, '首次请求无 ETag');
  const b = await req(p, { 'If-None-Match': a.etag });
  const bad = [];
  if (b.status !== 304) bad.push('条件请求返回 ' + b.status + '（期望 304）');
  if (b.bytes > 0) bad.push('304 仍回传正文 ' + b.bytes + ' 字节');
  L.done(bad.length === 0, bad.length ? bad.join('; ') : 'ETag=' + a.etag + '，304 零正文');
})().catch(e => L.done(false, '异常: ' + e.message));
