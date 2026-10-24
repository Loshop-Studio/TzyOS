# -*- coding: utf-8 -*-
"""Tzy OS updater: bump ?v= versions in entry HTML so tablets fetch changed
files without clearing cache.

Usage:
    python nova_update.py             check + apply
    python nova_update.py --dry-run   only report what would change

Also available at runtime: admin page button or POST /api/update.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from novacore.configutil import load_config  # noqa: E402
from novacore import toolkit  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Tzy OS cache-version updater")
    ap.add_argument("--dry-run", action="store_true", help="report only, no write")
    args = ap.parse_args()

    load_config()
    root = toolkit.novaos_dir()
    print("novaos dir:", root)
    if not os.path.isdir(root):
        print("[!] novaos_dir not found")
        sys.exit(1)

    if args.dry_run:
        import os as _os
        import re
        # 与 toolkit.bump_entry_versions 使用同一条正则，否则 --dry-run 会漏报
        # 无 ?v= 或非数字版本的引用（如新加的 apps/search.js）。
        pat = re.compile(r'((?:src|href)=")((?!https?:|data:|blob:|#|//)[^"?#]+)(?:\?[^"#]*)?(#[^"]*)?(")', re.I)
        hits = set()
        for dirpath, dirnames, filenames in _os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in
                           {"dist", "archive", "logs", "node_modules", "__pycache__", ".git"}]
            for name in filenames:
                if not name.endswith(".html"):
                    continue
                html = open(_os.path.join(dirpath, name), encoding="utf-8").read()
                for m in pat.finditer(html):
                    rel = m.group(2)
                    if rel.startswith("/"):
                        fp = _os.path.join(root, rel.lstrip("/").replace("/", _os.sep))
                    else:
                        fp = _os.path.normpath(_os.path.join(dirpath, rel.replace("/", _os.sep)))
                    if _os.path.isfile(fp):
                        hits.add(rel)
        for h in sorted(hits):
            print("  would check:", h)
        print("total referenced files:", len(hits))
        return

    changed, errors = toolkit.bump_entry_versions()
    if errors:
        print("[!] errors:", "; ".join(errors))
        sys.exit(1)
    print("updated references: %d" % len(changed))
    for c in changed:
        print("  -", c)
    print("done. tablets pick up changes on next open (entry HTML is ETag/304).")


if __name__ == "__main__":
    main()
