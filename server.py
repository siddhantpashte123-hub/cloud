#!/usr/bin/env python3
"""
MyCloud — Private file storage for your VPS.
Stdlib only. No Docker. No pip packages. Just Python 3.

Run:  python3 server.py --port 1919 --data ./data --config ./config.json
"""
import argparse
import hashlib
import hmac
import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import time
import urllib.parse
import zipfile
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

VERSION = "1.1.0"
DEFAULT_PORT = 1919
SESSION_TTL = 12 * 3600  # 12 hours
PBKDF2_ROUNDS = 200_000

# ---------------------------------------------------------------- config
CONFIG_PATH = Path(os.environ.get("MYCLOUD_CONFIG", "./config.json"))
DATA_DIR = Path(os.environ.get("MYCLOUD_DATA", "./data"))
PORT = int(os.environ.get("MYCLOUD_PORT", DEFAULT_PORT))

sessions = {}  # token -> expiry timestamp
login_attempts = {}  # ip -> (count, last_time)


def load_config():
    if not CONFIG_PATH.exists():
        return None
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return None


def save_config(cfg):
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    os.chmod(tmp, 0o600)
    tmp.replace(CONFIG_PATH)
    os.chmod(CONFIG_PATH, 0o600)


def hash_password(password: str, salt: bytes = None):
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PBKDF2_ROUNDS)
    return salt.hex(), dk.hex()


def verify_password(password: str, cfg) -> bool:
    try:
        salt = bytes.fromhex(cfg["salt"])
        expected = cfg["hash"]
    except Exception:
        return False
    _, dk = hash_password(password, salt)
    return hmac.compare_digest(dk, expected)


def safe_join(base: Path, user_path: str) -> Path:
    """Resolve user_path inside base, block traversal."""
    base = base.resolve()
    # strip leading slashes, decode
    p = urllib.parse.unquote(user_path or "")
    p = p.lstrip("/")
    target = (base / p).resolve()
    if target != base and base not in target.parents:
        raise ValueError("Path outside data dir")
    return target


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024
    return f"{n:.1f} TB"


def dir_stats(base: Path):
    total, files, folders = 0, 0, 0
    for root, dirs, filenames in os.walk(base):
        folders += len(dirs)
        for f in filenames:
            try:
                total += (Path(root) / f).stat().st_size
                files += 1
            except OSError:
                pass
    return total, files, folders


# ---------------------------------------------------------------- HTML UI
# UI redesigned following the unslop method by Damir Sokolovsky (@heydamir),
# https://github.com/heydamir/unslop — CC BY 4.0
#
# Direction:
#   Product   MyCloud — private file storage, sirf owner ka password
#   Character shaant, niji, saaf-suthra (calm, personal, meticulous)
#   World     raat ka aasmaan + desk lamp — ink-tinted dark, ember accent
#   Moments   asli photo thumbnails, media preview
#   Signature flat cloud + keyhole mark (single accent color)

FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
           "fill='none' stroke='%23e06c3a' stroke-width='1.8' stroke-linecap='round' "
           "stroke-linejoin='round'%3E%3Cpath d='M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z'/%3E"
           "%3Ccircle cx='11' cy='13' r='1.5' fill='%23e06c3a'/%3E%3Cpath d='M11 14v2.4'/%3E%3C/svg%3E")

CLOUD_MARK = ("<svg viewBox='0 0 24 24' fill='none' stroke='currentColor' stroke-width='1.6' "
              "stroke-linecap='round' stroke-linejoin='round'><path d='M18 10h-1.26A8 8 0 1 0 9 20h9"
              "a5 5 0 0 0 0-10z'/><circle cx='11' cy='13' r='1.5' fill='currentColor' stroke='none'/>"
              "<path d='M11 14v2.4'/></svg>")

BASE_CSS = """
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg0:#07080d; --bg:#0b0d14; --card:#11141c; --raise:#1a1e28;
  --line:rgba(238,240,245,.08);
  --txt:#eef0f5; --txt2:#a3a9b8; --mut:#7a8296;
  --acc:#e06c3a; --acc-ink:#1c1108;
  --pos:#4cc38a; --neg:#e5484d;
  --r-ctl:8px; --r-card:12px; --r-modal:14px;
  --ease:cubic-bezier(.22,1,.36,1);
}
html{color-scheme:dark}
body{font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;
  background:var(--bg);color:var(--txt);min-height:100vh;font-size:14px;line-height:1.5;
  -webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
::selection{background:rgba(224,108,58,.28)}
a{color:inherit;text-decoration:none}
button{font:inherit;color:inherit;background:none;border:none;cursor:pointer}
input{font:inherit;color:var(--txt)}
:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.num,.size,.mt,td.num{font-variant-numeric:tabular-nums}
mark{color:var(--acc);background:none}
"""

ICONS_CSS = """
.ic svg{display:block}
i.ico{display:inline-flex;width:16px;height:16px;flex:none;color:var(--mut)}
i.ico svg{width:16px;height:16px}
"""

LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCloud — Private Storage</title>
<link rel="icon" href="__FAVICON__">
<style>__BASE_CSS__
body{display:flex;align-items:center;justify-content:center;padding:24px}
.card{width:100%;max-width:360px;background:var(--card);border:1px solid var(--line);
  border-radius:var(--r-modal);padding:32px;text-align:center;
  animation:rise .42s var(--ease) both}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}
.mark{width:44px;height:44px;margin:0 auto 16px;color:var(--acc)}
.mark svg{width:44px;height:44px}
h1{font-size:18px;font-weight:600;letter-spacing:0}
.sub{color:var(--txt2);font-size:13px;margin:4px 0 24px}
.err{background:rgba(229,72,77,.1);border:1px solid rgba(229,72,77,.28);color:#f2a6aa;
  border-radius:var(--r-ctl);padding:10px 12px;font-size:13px;margin-bottom:16px;text-align:left}
input{width:100%;height:40px;padding:0 12px;background:var(--raise);
  border:1px solid var(--line);border-radius:var(--r-ctl);font-size:14px;margin-bottom:12px;
  transition:border-color .12s var(--ease)}
input::placeholder{color:var(--mut)}
input:focus{border-color:var(--acc);outline:none}
button{width:100%;height:40px;background:var(--acc);color:var(--acc-ink);font-weight:500;
  border-radius:var(--r-ctl);font-size:14px;box-shadow:inset 0 1px 0 rgba(255,255,255,.18);
  transition:background .12s var(--ease),transform .08s var(--ease)}
button:hover{background:#eb7743}
button:active{transform:scale(.985)}
.note{color:var(--mut);font-size:12px;margin-top:20px}
.note span{margin:0 6px}
</style></head><body>
<form class="card" method="POST" action="/login">
<div class="mark">__CLOUD_MARK__</div>
<h1>MyCloud</h1>
<div class="sub">Private storage — sirf aapke liye</div>
%%ERR%%
<input type="password" name="password" placeholder="Password" required autofocus autocomplete="current-password">
<button type="submit">Unlock</button>
<div class="note">Port 1919<span>·</span>Self-hosted<span>·</span>No Docker</div>
</form></body></html>"""

APP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCloud</title>
<link rel="icon" href="__FAVICON__">
<style>__BASE_CSS__
__ICONS_CSS__
body{display:flex;flex-direction:column}
.top{height:56px;display:flex;align-items:center;gap:12px;padding:0 20px;background:var(--bg0);
  border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20}
.brand{display:flex;align-items:center;gap:8px;font-weight:600;font-size:16px;flex:none}
.brand .mark{width:22px;height:22px;color:var(--acc);display:inline-flex}
.brand .mark svg{width:22px;height:22px}
.search{flex:1;display:flex;justify-content:center;min-width:0}
.search .box{position:relative;width:100%;max-width:432px}
.search i.ico{position:absolute;left:12px;top:50%;transform:translateY(-50%);pointer-events:none}
.search input{width:100%;height:36px;padding:0 12px 0 36px;background:var(--raise);
  border:1px solid var(--line);border-radius:var(--r-ctl);font-size:14px;
  transition:border-color .12s var(--ease)}
.search input::placeholder{color:var(--mut)}
.search input:focus{border-color:var(--acc);outline:none}
.top .spacer{flex:0}
.btn{display:inline-flex;align-items:center;gap:8px;height:36px;padding:0 14px;font-size:14px;
  font-weight:500;border-radius:var(--r-ctl);color:var(--txt2);background:var(--raise);
  border:1px solid var(--line);transition:color .12s var(--ease),background .12s var(--ease),
  border-color .12s var(--ease),transform .08s var(--ease);white-space:nowrap}
.btn:hover{color:var(--txt);background:#20242f}
.btn:active{transform:scale(.98)}
.btn.primary{background:var(--acc);color:var(--acc-ink);border-color:transparent;
  box-shadow:inset 0 1px 0 rgba(255,255,255,.18)}
.btn.primary:hover{background:#eb7743;color:var(--acc-ink)}
.iconbtn{display:inline-flex;align-items:center;justify-content:center;width:32px;height:32px;
  border-radius:var(--r-ctl);color:var(--txt2);border:1px solid var(--line);background:var(--raise);
  transition:color .12s var(--ease),background .12s var(--ease)}
.iconbtn:hover{color:var(--txt);background:#20242f}
.layout{display:flex;flex:1;min-height:0}
.side{width:248px;flex:none;background:var(--bg0);border-right:1px solid var(--line);
  padding:16px 12px;display:flex;flex-direction:column;gap:24px;overflow-y:auto}
.nav-label{font-size:12px;color:var(--mut);margin:0 12px 8px;letter-spacing:.01em}
.nav a{display:flex;align-items:center;gap:10px;height:36px;padding:0 12px;border-radius:var(--r-ctl);
  color:var(--txt2);font-size:14px;transition:color .12s var(--ease),background .12s var(--ease);
  position:relative}
.nav a + a{margin-top:2px}
.nav a:hover{color:var(--txt);background:var(--raise)}
.nav a.on{color:var(--txt);background:var(--raise)}
.nav a.on::before{content:"";position:absolute;left:0;top:8px;bottom:8px;width:2px;
  border-radius:2px;background:var(--acc)}
.nav a.on i.ico{color:var(--acc)}
.storage{padding:0 12px}
.storage .lab{font-size:12px;color:var(--mut);display:flex;align-items:center;gap:8px}
.storage .val{font-size:13px;color:var(--txt2);margin-top:8px;font-variant-numeric:tabular-nums}
.storage .bar{height:4px;background:var(--raise);border-radius:2px;overflow:hidden;margin-top:8px}
.storage .bar i{display:block;height:100%;width:0;background:var(--acc);border-radius:2px;
  transition:width .36s var(--ease)}
.side .actions{display:flex;flex-direction:column;gap:8px;padding:0 12px}
.side .actions .btn{width:100%;justify-content:flex-start}
.main{flex:1;min-width:0;padding:24px 32px 64px;max-width:1440px}
.crumbs{display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:14px;margin-bottom:16px}
.crumbs a{color:var(--txt2);padding:2px 6px;border-radius:6px;
  transition:color .12s var(--ease),background .12s var(--ease)}
.crumbs a:hover{color:var(--txt);background:var(--raise)}
.crumbs .sep{color:var(--mut)}
.crumbs .here{color:var(--mut);padding:2px 6px}
.chips{display:none;gap:8px;overflow-x:auto;padding-bottom:4px;margin-bottom:12px}
.chips .btn{height:32px;font-size:13px;flex:none}
.chips .btn.on{color:var(--txt);border-color:var(--acc)}
.drop{border:1px dashed rgba(238,240,245,.16);border-radius:var(--r-card);padding:16px 20px;
  display:flex;align-items:center;gap:12px;color:var(--mut);font-size:13px;margin-bottom:16px;
  transition:border-color .16s var(--ease),color .16s var(--ease),background .16s var(--ease)}
.drop.over{border-color:var(--acc);color:var(--txt);background:rgba(224,108,58,.06)}
.drop .grow{flex:1}
.prog{height:4px;background:var(--raise);border-radius:2px;overflow:hidden;margin-bottom:16px;display:none}
.prog.show{display:block}
.prog i{display:block;height:100%;width:0;background:var(--acc);transition:width .12s linear}
.utext{font-size:12px;color:var(--mut);margin:-8px 0 16px;display:none;font-variant-numeric:tabular-nums}
.utext.show{display:block}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:20px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:16px}
.item{background:var(--card);border:1px solid var(--line);border-radius:var(--r-card);
  padding:8px;cursor:pointer;position:relative;
  transition:border-color .12s var(--ease),background .12s var(--ease)}
.item:hover{border-color:rgba(238,240,245,.18);background:#131722}
.thumb{height:112px;border-radius:8px;background:var(--bg);display:flex;align-items:center;
  justify-content:center;overflow:hidden;color:var(--mut)}
.thumb svg{width:28px;height:28px}
.thumb img{width:100%;height:100%;object-fit:cover;display:block}
.meta{padding:8px 4px 2px}
.nm{font-size:13px;color:var(--txt);word-break:break-all;line-height:1.35;max-height:2.7em;
  overflow:hidden}
.mt{font-size:12px;color:var(--mut);margin-top:2px}
.acts{position:absolute;top:12px;right:12px;display:flex;gap:4px;opacity:0;
  transition:opacity .12s var(--ease)}
.item:hover .acts,.item:focus-within .acts{opacity:1}
.acts .iconbtn{width:28px;height:28px;background:rgba(11,13,20,.82);backdrop-filter:blur(4px)}
.acts .iconbtn svg{width:14px;height:14px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);
  border-radius:var(--r-card);overflow:hidden}
th,td{padding:0 16px;height:40px;text-align:left;font-size:13px;border-bottom:1px solid var(--line);
  font-weight:400}
th{color:var(--mut);font-size:12px;height:36px;background:var(--bg0)}
tr:last-child td{border-bottom:none}
tbody tr{transition:background .12s var(--ease)}
tbody tr:hover{background:var(--raise)}
td .rowic{display:inline-flex;width:16px;height:16px;color:var(--mut);vertical-align:-3px;margin-right:8px}
td .rowic svg{width:16px;height:16px}
td .nm2{color:var(--txt)}
td.size,td.mtime{color:var(--txt2);white-space:nowrap}
.empty{text-align:center;color:var(--mut);padding:64px 24px}
.empty svg{width:32px;height:32px;margin-bottom:12px;opacity:.7}
.empty .t{font-size:14px;color:var(--txt2)}
.empty .s{font-size:13px;margin-top:4px}
.modal{position:fixed;inset:0;background:rgba(4,5,9,.64);display:none;align-items:center;
  justify-content:center;z-index:50;padding:16px}
.modal.open{display:flex}
.dlg{background:var(--card);border:1px solid var(--line);border-radius:var(--r-modal);
  padding:24px;width:100%;max-width:400px;animation:pop .22s var(--ease) both}
@keyframes pop{from{opacity:0;transform:translateY(8px) scale(.98)}to{opacity:1;transform:none}}
.dlg h3{font-size:15px;font-weight:500;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.dlg input{width:100%;height:40px;padding:0 12px;background:var(--raise);border:1px solid var(--line);
  border-radius:var(--r-ctl);font-size:14px;margin-bottom:16px}
.dlg input:focus{border-color:var(--acc);outline:none}
.dlg .row{display:flex;gap:8px;justify-content:flex-end}
.dlg.wide{max-width:864px;padding:0;overflow:hidden}
.dlg.wide .dh{display:flex;align-items:center;gap:8px;padding:14px 16px;border-bottom:1px solid var(--line)}
.dlg.wide .dh .t{flex:1;font-size:14px;font-weight:500;white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
.dlg.wide .db{padding:24px;display:flex;justify-content:center;background:var(--bg)}
.media{max-width:100%;max-height:72vh;border-radius:var(--r-ctl)}
pre.txt{text-align:left;background:var(--bg0);border:1px solid var(--line);padding:16px;
  border-radius:var(--r-ctl);overflow:auto;max-height:60vh;font-size:12.5px;line-height:1.55;
  width:100%;color:var(--txt2);font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
.toast{position:fixed;bottom:24px;left:50%;transform:translate(-50%,8px);background:var(--raise);
  border:1px solid var(--line);padding:10px 16px;border-radius:var(--r-ctl);z-index:99;font-size:13px;
  color:var(--txt);opacity:0;pointer-events:none;transition:opacity .18s var(--ease),
  transform .18s var(--ease)}
.toast.show{opacity:1;transform:translate(-50%,0)}
.toast.err{color:#f2a6aa;border-color:rgba(229,72,77,.32)}
.fadein{animation:rise .32s var(--ease) both}
@media(max-width:760px){
  .side{display:none}
  .chips{display:flex}
  .main{padding:16px 16px 48px}
  .top{padding:0 12px}
  .grid{grid-template-columns:repeat(auto-fill,minmax(132px,1fr));gap:12px}
  .brand .wm{display:none}
}
</style></head><body>
<header class="top">
  <div class="brand"><span class="mark">__CLOUD_MARK__</span><span class="wm">My<mark>Cloud</mark></span></div>
  <div class="search"><div class="box"><i class="ico" id="ic-search"></i><input id="q" placeholder="Search" aria-label="Search files"></div></div>
  <button class="iconbtn" id="viewBtn" title="Grid / List"></button>
  <form method="POST" action="/logout" style="margin:0"><button class="btn" id="logoutBtn" type="submit"></button></form>
</header>
<div class="layout">
<aside class="side">
  <div>
    <div class="nav-label">Library</div>
    <nav class="nav" id="nav"></nav>
  </div>
  <div class="storage">
    <div class="lab"><i class="ico" id="ic-drive"></i>Storage</div>
    <div class="bar"><i id="storBar"></i></div>
    <div class="val" id="storTxt">—</div>
  </div>
  <div class="actions">
    <button class="btn" id="btnMkdir"></button>
    <button class="btn primary" id="btnUploadSide"></button>
  </div>
</aside>
<main class="main">
  <div class="crumbs" id="crumbs"></div>
  <div class="chips" id="chips"></div>
  <div class="drop" id="drop"><i class="ico" id="ic-drop"></i><span class="grow" id="dropTxt">Files yahan drop karo, ya Upload dabao</span></div>
  <div class="prog" id="prog"><i></i></div>
  <div class="utext" id="utext"></div>
  <div class="toolbar">
    <button class="btn" id="btnMkdir2"></button>
    <button class="btn primary" id="btnUpload"></button>
    <button class="btn" id="btnZip"></button>
  </div>
  <div id="content"></div>
</main>
</div>
<input type="file" id="fileInput" multiple hidden>

<div class="modal" id="mkdirModal"><div class="dlg"><h3 id="tMkdir">New folder</h3>
<input id="folderName" placeholder="Folder ka naam" autocomplete="off">
<div class="row"><button class="btn" data-close="mkdirModal">Cancel</button><button class="btn primary" id="btnMkdirGo">Create</button></div></div></div>

<div class="modal" id="renameModal"><div class="dlg"><h3>Rename</h3>
<input id="renameInput" autocomplete="off">
<div class="row"><button class="btn" data-close="renameModal">Cancel</button><button class="btn primary" id="btnRenameGo">Rename</button></div></div></div>

<div class="modal" id="previewModal"><div class="dlg wide">
<div class="dh"><span class="t" id="prevTitle"></span><button class="iconbtn" data-close="previewModal" id="prevClose"></button></div>
<div class="db" id="prevBody"></div></div></div>

<div class="toast" id="toast"></div>

<script>
const $ = id => document.getElementById(id);
/* ------- icons: single set, 1.5 stroke, currentColor ------- */
const P = {
  folder:'<path d="M4 20h16a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13c0 1.1.9 2 2 2Z"/>',
  folderOpen:'<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
  image:'<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.086-3.086a2 2 0 0 0-2.828 0L6 21"/>',
  film:'<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/><path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>',
  music:'<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
  fileText:'<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/><path d="M10 9H8"/><path d="M16 13H8"/><path d="M16 17H8"/>',
  file:'<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
  archive:'<rect width="20" height="5" x="2" y="3" rx="1"/><path d="M4 8v11a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8"/><path d="M10 12h4"/>',
  download:'<path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/>',
  upload:'<path d="M12 15V3"/><path d="m7 8 5-5 5 5"/><path d="M5 21h14"/>',
  pencil:'<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
  trash:'<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
  plus:'<path d="M5 12h14"/><path d="M12 5v14"/>',
  search:'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
  grid:'<rect width="7" height="7" x="3" y="3" rx="1"/><rect width="7" height="7" x="14" y="3" rx="1"/><rect width="7" height="7" x="14" y="14" rx="1"/><rect width="7" height="7" x="3" y="14" rx="1"/>',
  list:'<path d="M3 6h.01"/><path d="M3 12h.01"/><path d="M3 18h.01"/><path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/>',
  x:'<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
  chev:'<path d="m9 18 6-6-6-6"/>',
  logout:'<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/>',
  drive:'<path d="M22 12H2"/><path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/><path d="M6 16h.01"/><path d="M10 16h.01"/>'
};
const ic = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round">${P[n]}</svg>`;
const IMG_EXTS = ["jpg","jpeg","png","gif","webp","svg","avif"];
const VID_EXTS = ["mp4","mkv","webm","mov","m4v"];
const AUD_EXTS = ["mp3","wav","ogg","flac","m4a"];
const DOC_EXTS = ["pdf","txt","md","doc","docx","ppt","pptx","xls","xlsx","csv"];
const TXT_EXTS = ["txt","md","py","js","ts","html","css","json","log","xml","yml","yaml","sh","csv"];
const ZIP_EXTS = ["zip","rar","tar","gz","7z","bz2"];
const extOf = n => n.includes(".") ? n.split(".").pop().toLowerCase() : "";
function iconFor(f){
  if (f.is_dir) return "folder";
  const e = extOf(f.name);
  if (IMG_EXTS.includes(e)) return "image";
  if (VID_EXTS.includes(e)) return "film";
  if (AUD_EXTS.includes(e)) return "music";
  if (ZIP_EXTS.includes(e)) return "archive";
  if (DOC_EXTS.includes(e) || TXT_EXTS.includes(e)) return "fileText";
  return "file";
}
function esc(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function fmt(n){if(n<1024)return n+" B";for(const u of["KB","MB","GB","TB"]){n/=1024;if(n<1024||u==="TB")return n.toFixed(1)+" "+u}return n+" B"}

/* ------- static chrome ------- */
$("ic-search").innerHTML = ic("search");
$("ic-drive").innerHTML = ic("drive");
$("ic-drop").innerHTML = ic("upload");
$("prevClose").innerHTML = ic("x");
$("logoutBtn").innerHTML = ic("logout") + "<span>Log out</span>";
$("btnMkdir").innerHTML = ic("plus") + "<span>New folder</span>";
$("btnMkdir2").innerHTML = ic("plus") + "<span>New folder</span>";
$("btnUpload").innerHTML = ic("upload") + "<span>Upload</span>";
$("btnUploadSide").innerHTML = ic("upload") + "<span>Upload</span>";
$("btnZip").innerHTML = ic("archive") + "<span>Download ZIP</span>";
$("viewBtn").innerHTML = ic("list");

const NAV = [["","Home","folderOpen"],["image","Photos","image"],["video","Videos","film"],["audio","Music","music"],["doc","Documents","fileText"]];
function buildNav(){
  $("nav").innerHTML = NAV.map(([k,label,icn]) =>
    `<a href="#" data-k="${k}"><i class="ico">${ic(icn)}</i>${label}</a>`).join("");
  $("chips").innerHTML = NAV.map(([k,label]) =>
    `<button class="btn" data-k="${k}">${label}</button>`).join("");
  document.querySelectorAll("#nav a, #chips .btn").forEach(el =>
    el.addEventListener("click", e => { e.preventDefault(); go2(el.dataset.k); }));
}
function markNav(){
  document.querySelectorAll("#nav a, #chips .btn").forEach(el => {
    const k = el.dataset.k;
    const on = k === "" ? (typeFilter === "" ) : (typeFilter === k);
    el.classList.toggle("on", on);
  });
}

/* ------- state ------- */
let curPath = "", files = [], view = "grid", typeFilter = "", renameTarget = "";

function toast(msg, bad){
  const t = $("toast"); t.textContent = msg;
  t.classList.toggle("err", !!bad); t.classList.add("show");
  clearTimeout(t._h); t._h = setTimeout(() => t.classList.remove("show"), 2400);
}
function openModal(id){ $(id).classList.add("open"); const inp = $(id).querySelector("input"); if (inp) setTimeout(()=>inp.focus(), 40); }
function closeModal(id){ $(id).classList.remove("open"); if (id === "previewModal") $("prevBody").innerHTML = ""; }

function go(p){ curPath = p; typeFilter = ""; load(); }
function go2(k){ if (k === "") { typeFilter = ""; } else { typeFilter = k; curPath = ""; } load(); }

async function load(){
  const r = await fetch("/api/files?path=" + encodeURIComponent(curPath));
  if (r.status === 401) { location.reload(); return; }
  const d = await r.json();
  files = d.files || [];
  $("storBar").style.width = d.disk_total ? Math.min(100, (d.used_bytes / d.disk_total) * 100) + "%" : "0%";
  $("storTxt").textContent = (d.used || "0 B") + " · " + (d.count || 0) + " files";
  renderCrumbs(); markNav(); render();
}
function renderCrumbs(){
  const parts = curPath ? curPath.split("/").filter(Boolean) : [];
  let h = `<a href="#" data-go="">Home</a>`;
  parts.forEach((p, i) => {
    const path = parts.slice(0, i + 1).join("/");
    const last = i === parts.length - 1;
    h += `<span class="sep">${ic("chev")}</span>` + (last
      ? `<span class="here">${esc(p)}</span>`
      : `<a href="#" data-go="${esc(path)}">${esc(p)}</a>`);
  });
  const c = $("crumbs"); c.innerHTML = h;
  c.querySelectorAll("a[data-go]").forEach(a => a.addEventListener("click", e => { e.preventDefault(); go(a.dataset.go); }));
}
function visible(){
  let v = files;
  if (typeFilter) v = v.filter(f => {
    if (f.is_dir) return false;
    const e = extOf(f.name);
    if (typeFilter === "image") return IMG_EXTS.includes(e);
    if (typeFilter === "video") return VID_EXTS.includes(e);
    if (typeFilter === "audio") return AUD_EXTS.includes(e);
    if (typeFilter === "doc")   return DOC_EXTS.includes(e) || TXT_EXTS.includes(e);
    return false;
  });
  const q = $("q").value.trim().toLowerCase();
  if (q) v = v.filter(f => f.name.toLowerCase().includes(q));
  return v;
}
function render(){
  const v = visible(), c = $("content");
  $("viewBtn").innerHTML = ic(view === "grid" ? "list" : "grid");
  if (!v.length){
    c.innerHTML = `<div class="empty fadein">${ic("folderOpen")}
      <div class="t">Yahan kuch nahi hai</div>
      <div class="s">Files drop karo ya Upload dabao</div></div>`;
    return;
  }
  if (view === "grid"){
    c.innerHTML = `<div class="grid fadein">` + v.map(f => {
      const p = esc(f.path), n = esc(f.name);
      const thumb = (!f.is_dir && IMG_EXTS.includes(extOf(f.name)))
        ? `<img loading="lazy" src="/api/download?path=${encodeURIComponent(f.path)}&inline=1" alt="">`
        : ic(iconFor(f));
      return `<div class="item" tabindex="0" data-open="${p}" data-dir="${f.is_dir ? 1 : 0}" data-name="${n}">
        <div class="acts">
          ${f.is_dir ? "" : `<button class="iconbtn" title="Download" data-dl="${p}">${ic("download")}</button>`}
          <button class="iconbtn" title="Rename" data-rn="${p}" data-name="${n}">${ic("pencil")}</button>
          <button class="iconbtn" title="Delete" data-del="${p}" data-name="${n}">${ic("trash")}</button>
        </div>
        <div class="thumb">${thumb}</div>
        <div class="meta"><div class="nm">${n}</div><div class="mt">${f.is_dir ? "Folder" : fmt(f.size)}</div></div>
      </div>`;
    }).join("") + `</div>`;
  } else {
    c.innerHTML = `<table class="fadein"><thead><tr><th>Naam</th><th style="width:110px">Size</th><th style="width:150px">Modified</th><th style="width:124px"></th></tr></thead><tbody>` +
      v.map(f => {
        const p = esc(f.path), n = esc(f.name);
        return `<tr>
          <td><span class="rowic">${ic(iconFor(f))}</span><a href="#" class="nm2" data-open="${p}" data-dir="${f.is_dir ? 1 : 0}" data-name="${n}">${n}</a></td>
          <td class="size">${f.is_dir ? "—" : fmt(f.size)}</td><td class="mtime">${f.mtime}</td>
          <td style="white-space:nowrap;text-align:right">
            ${f.is_dir ? "" : `<button class="iconbtn" title="Download" data-dl="${p}">${ic("download")}</button> `}
            <button class="iconbtn" title="Rename" data-rn="${p}" data-name="${n}">${ic("pencil")}</button>
            <button class="iconbtn" title="Delete" data-del="${p}" data-name="${n}">${ic("trash")}</button>
          </td></tr>`;
      }).join("") + `</tbody></table>`;
  }
}

/* ------- actions ------- */
function dl(p){ location.href = "/api/download?path=" + encodeURIComponent(p); }
function askRename(p, n){ renameTarget = p; $("renameInput").value = n; openModal("renameModal"); }
async function del(p, n){
  if (!confirm(`"${n}" delete karna hai? Ye wapas nahi aayega.`)) return;
  const r = await fetch("/api/delete", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:p})});
  toast(r.ok ? "Delete ho gaya" : "Delete nahi hua", !r.ok); load();
}
async function doRename(){
  const name = $("renameInput").value.trim(); if (!name) return;
  const r = await fetch("/api/rename", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:renameTarget, name})});
  closeModal("renameModal"); toast(r.ok ? "Rename ho gaya" : "Rename nahi hua", !r.ok); load();
}
async function mkdir(){
  const name = $("folderName").value.trim(); if (!name) return;
  const r = await fetch("/api/mkdir", {method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify({path:curPath, name})});
  closeModal("mkdirModal"); $("folderName").value = "";
  toast(r.ok ? "Folder ban gaya" : "Folder nahi bana", !r.ok); load();
}
async function preview(p, n){
  const e = extOf(n), u = "/api/download?path=" + encodeURIComponent(p) + "&inline=1";
  $("prevTitle").textContent = n;
  let h = "";
  if (IMG_EXTS.includes(e)) h = `<img class="media" src="${u}">`;
  else if (VID_EXTS.includes(e)) h = `<video class="media" controls autoplay src="${u}"></video>`;
  else if (AUD_EXTS.includes(e)) h = `<audio controls autoplay src="${u}" style="width:100%"></audio>`;
  else if (e === "pdf") h = `<iframe src="${u}" style="width:100%;height:72vh;border:none;border-radius:8px;background:#fff"></iframe>`;
  else if (TXT_EXTS.includes(e)){
    const r = await fetch(u);
    h = `<pre class="txt">${esc((await r.text()).slice(0, 40000))}</pre>`;
  }
  else h = `<div class="empty">${ic("file")}<div class="t">Preview nahi hai</div><div class="s"><a href="${u.replace("&inline=1","")}" style="color:var(--acc)">Download karo</a></div></div>`;
  $("prevBody").innerHTML = h;
  openModal("previewModal");
}

/* ------- upload with progress ------- */
function uploadFiles(list){
  if (!list || !list.length) return;
  const prog = $("prog"), bar = prog.querySelector("i"), ut = $("utext");
  prog.classList.add("show"); ut.classList.add("show");
  let i = 0, failed = 0;
  const step = () => {
    if (i >= list.length){
      prog.classList.remove("show"); ut.classList.remove("show"); bar.style.width = "0";
      $("fileInput").value = "";
      toast(failed ? `${list.length - failed} upload hui, ${failed} fail` : (list.length > 1 ? list.length + " files upload ho gayi" : "File upload ho gayi"), !!failed);
      load(); return;
    }
    const f = list[i]; i++;
    ut.textContent = `Uploading ${i} of ${list.length} — ${f.name}`;
    const fd = new FormData(); fd.append("path", curPath); fd.append("file", f);
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload");
    xhr.upload.onprogress = e => { if (e.lengthComputable){
      bar.style.width = (((i - 1) + e.loaded / e.total) / list.length * 100) + "%"; } };
    xhr.onload = () => { if (xhr.status !== 200) failed++; bar.style.width = (i / list.length * 100) + "%"; step(); };
    xhr.onerror = () => { failed++; step(); };
    xhr.send(fd);
  };
  step();
}

/* ------- wiring ------- */
$("content").addEventListener("dblclick", e => {
  const el = e.target.closest("[data-open]"); if (!el) return;
  const p = el.dataset.open;
  if (el.dataset.dir === "1") go(p); else preview(p, el.dataset.name);
});
$("content").addEventListener("click", e => {
  const a = e.target.closest("a[data-open]");
  if (a){ e.preventDefault(); if (a.dataset.dir === "1") go(a.dataset.open); else preview(a.dataset.open, a.dataset.name); return; }
  const b = e.target.closest("button"); if (!b) return;
  if (b.dataset.dl){ dl(b.dataset.dl); }
  else if (b.dataset.rn){ askRename(b.dataset.rn, b.dataset.name); }
  else if (b.dataset.del){ del(b.dataset.del, b.dataset.name); }
});
$("content").addEventListener("keydown", e => {
  if (e.key !== "Enter") return;
  const el = e.target.closest(".item[data-open]"); if (!el) return;
  e.preventDefault();
  if (el.dataset.dir === "1") go(el.dataset.open); else preview(el.dataset.open, el.dataset.name);
});
$("q").addEventListener("input", render);
$("viewBtn").addEventListener("click", () => { view = view === "grid" ? "list" : "grid"; render(); });
$("btnMkdir").addEventListener("click", () => openModal("mkdirModal"));
$("btnMkdir2").addEventListener("click", () => openModal("mkdirModal"));
$("btnUpload").addEventListener("click", () => $("fileInput").click());
$("btnUploadSide").addEventListener("click", () => $("fileInput").click());
$("btnZip").addEventListener("click", () => { location.href = "/api/zip?path=" + encodeURIComponent(curPath); });
$("fileInput").addEventListener("change", e => uploadFiles(e.target.files));
$("btnMkdirGo").addEventListener("click", mkdir);
$("btnRenameGo").addEventListener("click", doRename);
$("folderName").addEventListener("keydown", e => { if (e.key === "Enter") mkdir(); });
$("renameInput").addEventListener("keydown", e => { if (e.key === "Enter") doRename(); });
document.querySelectorAll("[data-close]").forEach(b => b.addEventListener("click", () => closeModal(b.dataset.close)));
document.querySelectorAll(".modal").forEach(m => m.addEventListener("mousedown", e => { if (e.target === m) closeModal(m.id); }));
document.addEventListener("keydown", e => { if (e.key === "Escape") document.querySelectorAll(".modal.open").forEach(m => closeModal(m.id)); });

const drop = $("drop"), main = document.querySelector(".main");
["dragover","dragenter"].forEach(ev => main.addEventListener(ev, e => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave","drop"].forEach(ev => main.addEventListener(ev, e => { if (e.target === main || ev === "drop"){ e.preventDefault(); if (ev === "dragleave" && e.relatedTarget && main.contains(e.relatedTarget)) return; drop.classList.remove("over"); } }));
main.addEventListener("drop", e => { e.preventDefault(); drop.classList.remove("over"); uploadFiles(e.dataTransfer.files); });

buildNav();
load();
</script></body></html>"""

def _fill_ui(t):
    for k, v in (("__FAVICON__", FAVICON), ("__BASE_CSS__", BASE_CSS),
                 ("__ICONS_CSS__", ICONS_CSS), ("__CLOUD_MARK__", CLOUD_MARK)):
        t = t.replace(k, v)
    return t

LOGIN_HTML = _fill_ui(LOGIN_HTML)
APP_HTML = _fill_ui(APP_HTML)


# ---------------------------------------------------------------- multipart
def parse_multipart(body: bytes, content_type: str):
    """Minimal multipart/form-data parser (stdlib only)."""
    m = re.search(r"boundary=([^;]+)", content_type)
    if not m:
        raise ValueError("No boundary")
    boundary = m.group(1).strip().strip('"').encode()
    parts = body.split(b"--" + boundary)
    fields, files = {}, []
    for part in parts:
        if not part or part in (b"--\r\n", b"--", b"\r\n"):
            continue
        if part.startswith(b"--"):
            break
        # strip leading CRLF and trailing CRLF
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        head, sep, data = part.partition(b"\r\n\r\n")
        if not sep:
            continue
        headers = head.decode("utf-8", "replace")
        name_m = re.search(r'name="([^"]+)"', headers)
        if not name_m:
            continue
        name = name_m.group(1)
        file_m = re.search(r'filename="([^"]*)"', headers)
        if file_m:
            files.append((name, file_m.group(1), data))
        else:
            fields[name] = data.decode("utf-8", "replace")
    return fields, files


# ---------------------------------------------------------------- handler
class Handler(BaseHTTPRequestHandler):
    server_version = "MyCloud/1.0"

    def log_message(self, fmt, *args):
        sys.stderr.write("[%s] %s\n" % (self.log_date_time_string(), fmt % args))

    # ---- helpers
    def send_html(self, body, status=200):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, obj, status=200):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(data)

    def redirect(self, loc):
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def session_token(self):
        raw = self.headers.get("Cookie", "")
        c = cookies.SimpleCookie(raw)
        morsel = c.get("session")
        return morsel.value if morsel else None

    def authed(self):
        tok = self.session_token()
        if not tok:
            return False
        exp = sessions.get(tok)
        if not exp or exp < time.time():
            sessions.pop(tok, None)
            return False
        return True

    def require_auth(self):
        if not self.authed():
            self.send_json({"error": "unauthorized"}, 401)
            return False
        return True

    def read_body(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else b""

    def read_json(self):
        try:
            return json.loads(self.read_body() or b"{}")
        except Exception:
            return {}

    # ---- routes
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, qs = parsed.path, urllib.parse.parse_qs(parsed.query)

        if path == "/":
            if not self.authed():
                self.send_html(LOGIN_HTML.replace("%%ERR%%", ""))
            else:
                self.send_html(APP_HTML)
            return

        if path == "/health":
            self.send_json({"ok": True, "version": VERSION})
            return

        if path.startswith("/api/"):
            if not self.require_auth():
                return
            if path == "/api/files":
                self.handle_files(qs)
            elif path == "/api/download":
                self.handle_download(qs)
            elif path == "/api/zip":
                self.handle_zip(qs)
            else:
                self.send_json({"error": "not found"}, 404)
            return

        self.send_json({"error": "not found"}, 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/login":
            self.handle_login()
            return
        if path == "/logout":
            tok = self.session_token()
            if tok:
                sessions.pop(tok, None)
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax")
            self.end_headers()
            return

        if path.startswith("/api/"):
            if not self.require_auth():
                return
            if path == "/api/upload":
                self.handle_upload()
            elif path == "/api/delete":
                self.handle_delete()
            elif path == "/api/mkdir":
                self.handle_mkdir()
            elif path == "/api/rename":
                self.handle_rename()
            else:
                self.send_json({"error": "not found"}, 404)
            return

        self.send_json({"error": "not found"}, 404)

    # ---- auth
    def handle_login(self):
        ip = self.client_address[0]
        now = time.time()
        count, last = login_attempts.get(ip, (0, 0))
        if count >= 10 and now - last < 60:
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">Bahut zyada galat tries — 60 seconds ruko, phir try karo.</div>'), 429)
            return
        body = self.read_body().decode("utf-8", "replace")
        form = urllib.parse.parse_qs(body)
        password = (form.get("password") or [""])[0]
        cfg = load_config()
        if cfg is None:
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">Server setup nahi hua — pehle <b>cloud install</b> chalao.</div>'), 500)
            return
        if verify_password(password, cfg):
            login_attempts.pop(ip, None)
            tok = secrets.token_urlsafe(32)
            sessions[tok] = now + SESSION_TTL
            self.send_response(303)
            self.send_header("Location", "/")
            self.send_header("Set-Cookie", f"session={tok}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_TTL}")
            self.end_headers()
        else:
            login_attempts[ip] = (count + 1, now)
            time.sleep(1)  # slow down brute force
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">Galat password — dobara try karo.</div>'), 401)

    # ---- files
    def handle_files(self, qs):
        rel = (qs.get("path") or [""])[0]
        try:
            target = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        if not target.exists() or not target.is_dir():
            return self.send_json({"error": "not found"}, 404)
        items = []
        try:
            entries = sorted(target.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            return self.send_json({"error": str(e)}, 500)
        for e in entries:
            try:
                st = e.stat()
                items.append({
                    "name": e.name,
                    "path": str(e.relative_to(DATA_DIR.resolve())) if e != DATA_DIR.resolve() else "",
                    "is_dir": e.is_dir(),
                    "size": st.st_size if e.is_file() else 0,
                    "mtime": time.strftime("%Y-%m-%d %H:%M", time.localtime(st.st_mtime)),
                })
            except OSError:
                continue
        total, fcount, _ = dir_stats(DATA_DIR)
        try:
            disk = shutil.disk_usage(str(DATA_DIR))
            disk_total = disk.total
        except OSError:
            disk_total = 0
        self.send_json({
            "files": items,
            "used": fmt_size(total),
            "used_bytes": total,
            "count": fcount,
            "disk_total": disk_total,
        })

    def handle_download(self, qs):
        rel = (qs.get("path") or [""])[0]
        try:
            target = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        if not target.exists() or not target.is_file():
            return self.send_json({"error": "not found"}, 404)
        ctype, _ = mimetypes.guess_type(str(target))
        ctype = ctype or "application/octet-stream"
        inline = (qs.get("inline") or [""])[0] == "1"
        dispo = "inline" if inline else "attachment"
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'{dispo}; filename="{target.name}"')
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile, length=1024 * 256)

    def handle_zip(self, qs):
        rel = (qs.get("path") or [""])[0]
        try:
            target = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        if not target.exists() or not target.is_dir():
            return self.send_json({"error": "not found"}, 404)
        import io
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, _, filenames in os.walk(target):
                for fn in filenames:
                    full = Path(root) / fn
                    zf.write(full, full.relative_to(target))
        data = buf.getvalue()
        name = target.name or "files"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Content-Disposition", f'attachment; filename="{name}.zip"')
        self.end_headers()
        self.wfile.write(data)

    def handle_upload(self):
        ctype = self.headers.get("Content-Type", "")
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        try:
            fields, files = parse_multipart(body, ctype)
        except Exception as e:
            return self.send_json({"error": f"parse failed: {e}"}, 400)
        if not files:
            return self.send_json({"error": "no file"}, 400)
        rel = fields.get("path", "")
        try:
            dest_dir = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        dest_dir.mkdir(parents=True, exist_ok=True)
        saved = []
        for _, filename, data in files:
            if not filename:
                continue
            safe_name = Path(filename).name  # strip any path from filename
            if not safe_name or safe_name in (".", ".."):
                continue
            dest = dest_dir / safe_name
            # avoid overwrite: name (1).ext
            if dest.exists():
                stem, suffix = dest.stem, dest.suffix
                i = 1
                while dest.exists():
                    dest = dest_dir / f"{stem} ({i}){suffix}"
                    i += 1
            with open(dest, "wb") as f:
                f.write(data)
            saved.append(safe_name)
        self.send_json({"ok": True, "saved": saved})

    def handle_delete(self):
        data = self.read_json()
        rel = data.get("path", "")
        if not rel:
            return self.send_json({"error": "path required"}, 400)
        try:
            target = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        if target == DATA_DIR.resolve():
            return self.send_json({"error": "cannot delete root"}, 400)
        if not target.exists():
            return self.send_json({"error": "not found"}, 404)
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self.send_json({"ok": True})

    def handle_mkdir(self):
        data = self.read_json()
        rel, name = data.get("path", ""), (data.get("name") or "").strip()
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            return self.send_json({"error": "invalid name"}, 400)
        try:
            parent = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        target = parent / name
        if target.exists():
            return self.send_json({"error": "already exists"}, 409)
        target.mkdir(parents=True)
        self.send_json({"ok": True})

    def handle_rename(self):
        data = self.read_json()
        rel, name = data.get("path", ""), (data.get("name") or "").strip()
        if not rel or not name or "/" in name or "\\" in name or name in (".", ".."):
            return self.send_json({"error": "invalid name"}, 400)
        try:
            target = safe_join(DATA_DIR, rel)
        except ValueError:
            return self.send_json({"error": "invalid path"}, 400)
        if target == DATA_DIR.resolve():
            return self.send_json({"error": "cannot rename root"}, 400)
        if not target.exists():
            return self.send_json({"error": "not found"}, 404)
        dest = target.parent / name
        if dest.exists():
            return self.send_json({"error": "already exists"}, 409)
        target.rename(dest)
        self.send_json({"ok": True})


def main():
    global CONFIG_PATH, DATA_DIR, PORT
    ap = argparse.ArgumentParser(description="MyCloud private storage")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--data", default=str(DATA_DIR))
    ap.add_argument("--config", default=str(CONFIG_PATH))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--set-password", action="store_true", help="Set/change password and exit")
    args = ap.parse_args()

    CONFIG_PATH = Path(args.config)
    DATA_DIR = Path(args.data)
    PORT = args.port
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if args.set_password:
        import getpass
        pw = getpass.getpass("Naya password daalo: ")
        pw2 = getpass.getpass("Password dobara daalo: ")
        if pw != pw2 or len(pw) < 4:
            print("❌ Password match nahi hua ya bahut chhota hai (min 4 chars).")
            sys.exit(1)
        salt, h = hash_password(pw)
        save_config({"salt": salt, "hash": h, "port": PORT, "data": str(DATA_DIR), "version": VERSION})
        print("✅ Password set ho gaya!")
        return

    cfg = load_config()
    if cfg is None:
        print("⚠️  Config nahi mila. Pehle password set karo:")
        print("   python3 server.py --set-password")
        print("   Ya install script chalao:  bash install.sh")
        sys.exit(1)

    # config file values override defaults if present
    if "port" in cfg and "--port" not in sys.argv:
        PORT = int(cfg["port"])
    if "data" in cfg and "--data" not in sys.argv:
        DATA_DIR = Path(cfg["data"])
        DATA_DIR.mkdir(parents=True, exist_ok=True)

    server = ThreadingHTTPServer((args.host, PORT), Handler)
    print(f"☁️  MyCloud v{VERSION} running on http://0.0.0.0:{PORT}")
    print(f"📁 Data dir: {DATA_DIR.resolve()}")
    print("   Sirf aap — password protected 🔒  (Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Server band ho gaya.")


if __name__ == "__main__":
    main()
