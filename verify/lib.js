/* 验证用例公共库：启动浏览器、可选注入、断言、结果输出。
 * 每个用例脚本末尾调用 done(ok, detail) 即可。 */
const path = require('path');
const fs = require('fs');
const { chromium } = require('/tmp/node_modules/playwright-core');

const HOST = 'web-alicdn.zyai.cc';
const PORT = 8097;
const MOUNT = `http://${HOST}/__nova__/`;
const CHROME = '/snap/bin/chromium';
const HERE = __dirname;
const ROOT = path.dirname(HERE);
const SHOTS = path.join(HERE, 'logs', 'shots');

function ensureShots() { fs.mkdirSync(SHOTS, { recursive: true }); }

function done(ok, detail, extra) {
  const out = Object.assign({ ok: !!ok, detail: String(detail || '') }, extra || {});
  console.log('__RESULT__ ' + JSON.stringify(out));
  process.exit(0);
}

async function launch(opts) {
  opts = opts || {};
  const args = ['--no-sandbox', '--disable-dev-shm-usage'];
  const rules = [`MAP ${HOST} 127.0.0.1:${PORT}`];
  if (opts.hostLocal) rules.push(`MAP host.local 127.0.0.1:${opts.hostLocal}`);
  args.push('--host-resolver-rules=' + rules.join(','));
  return chromium.launch({ executablePath: CHROME, args });
}

/* 起一个极简宿主页服务（模拟被劫持的教学平台页面） */
function hostPageServer(port, html) {
  const http = require('http');
  const body = html || `<!DOCTYPE html><html><head><meta charset="utf-8">
    <title>在线专栏</title></head><body>
    <div id="app"><h1>教学平台</h1><input id="q" placeholder="搜索"></div>
    </body></html>`;
  return new Promise(resolve => {
    const srv = http.createServer((q, s) => {
      s.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      s.end(body);
    });
    srv.listen(port, '0.0.0.0', () => resolve(srv));
  });
}

function bootStubSource() {
  const src = fs.readFileSync(path.join(ROOT, 'boot-stub.js'), 'utf8');
  return src.split('__BOOT_BASE__').join(MOUNT);
}

/* 在宿主页里注入 stub */
async function injectStub(page) {
  await page.addScriptTag({ content: bootStubSource() });
  await page.waitForTimeout(1200);
}

function todayCode() {
  const d = new Date();
  return String(d.getMonth() + 1).padStart(2, '0') + String(d.getDate()).padStart(2, '0');
}

/* 走完整首次激活（口令 + MMDD） */
async function activate(page, keyword) {
  await page.fill('#q', keyword || 'cpt1104122');
  await page.waitForTimeout(400);
  await page.fill('#q', todayCode());
}

async function waitOsReady(page, ms) {
  const end = Date.now() + (ms || 25000);
  while (Date.now() < end) {
    for (const f of page.frames()) {
      try {
        if (await f.evaluate(() => !!(window.OS && window.OS.__booted))) return f;
      } catch (e) { /* 跨域瞬时不可访问 */ }
    }
    await page.waitForTimeout(400);
  }
  return null;
}

function shot(page, name) {
  ensureShots();
  return page.screenshot({ path: path.join(SHOTS, name + '.png') }).catch(() => {});
}

module.exports = {
  HOST, PORT, MOUNT, CHROME, ROOT, HERE, SHOTS,
  done, launch, hostPageServer, bootStubSource, injectStub, activate,
  waitOsReady, shot, todayCode,
};
