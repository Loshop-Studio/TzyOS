#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证用后端服务：监听 8097，供 verify/runner.py 起的用例访问。

与 novacore 的唯一差别是把 novaos_dir 指向 ./novaos2（本机没有 ./novaos），
其余完全复用生产代码路径，确保验证的就是真实逻辑。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.chdir(ROOT)

import novacore.configutil as cu          # noqa: E402

cu.load_config()
cu.CONFIG["novaos_dir"] = "./novaos2"

import novacore.proxy_app as pa           # noqa: E402

pa.novaos_dir = cu.novaos_dir
app = pa.create_proxy_app()

from werkzeug.serving import make_server   # noqa: E402

if __name__ == "__main__":
    port = int(os.environ.get("VERIFY_PORT", "8097"))
    srv = make_server("0.0.0.0", port, app, threaded=True)
    print("LISTENING %d" % port, flush=True)
    srv.serve_forever()
