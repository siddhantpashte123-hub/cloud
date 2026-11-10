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

VERSION = "1.0.0"
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
LOGIN_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCloud — Private Storage</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:#141414;color:#eee;min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px}
.card{background:#1e1e1e;border:1px solid #333;border-radius:16px;padding:36px 32px;width:100%;max-width:380px;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.5)}
.logo{font-size:44px;margin-bottom:6px}
h1{font-size:22px;margin-bottom:4px}
.sub{color:#999;font-size:13px;margin-bottom:24px}
.err{background:#3a1d1d;color:#ff8a8a;border:1px solid #7a2e2e;border-radius:8px;padding:10px;font-size:13px;margin-bottom:14px}
input{width:100%;padding:13px 14px;background:#141414;border:1px solid #444;border-radius:10px;color:#fff;font-size:15px;margin-bottom:12px;outline:none}
input:focus{border-color:#e06c3a}
button{width:100%;padding:13px;background:linear-gradient(135deg,#e06c3a,#c94f2a);border:none;border-radius:10px;color:#fff;font-size:15px;font-weight:700;cursor:pointer}
button:hover{opacity:.9}
.note{color:#777;font-size:12px;margin-top:16px}
</style></head><body>
<form class="card" method="POST" action="/login">
<div class="logo">☁️</div><h1>MyCloud</h1>
<div class="sub">Private storage — sirf aapke liye 🔒</div>
%%ERR%%
<input type="password" name="password" placeholder="Password daalo" required autofocus autocomplete="current-password">
<button type="submit">Unlock →</button>
<div class="note">Port 1919 • Password protected • No Docker needed</div>
</form></body></html>"""

APP_HTML = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>MyCloud — Files</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
:root{--bg:#141414;--panel:#1e1e1e;--panel2:#262626;--line:#333;--txt:#eee;--mut:#999;--acc:#e06c3a;--acc2:#c94f2a}
body{font-family:system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--txt);min-height:100vh}
a{color:inherit;text-decoration:none}
.top{display:flex;align-items:center;gap:14px;padding:12px 18px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:10;flex-wrap:wrap}
.top .brand{font-weight:800;font-size:18px}
.top .brand span{color:var(--acc)}
.search{flex:1;min-width:160px;max-width:420px}
.search input{width:100%;padding:9px 14px;background:var(--bg);border:1px solid var(--line);border-radius:20px;color:#fff;outline:none}
.search input:focus{border-color:var(--acc)}
.btn{padding:9px 16px;border-radius:10px;border:1px solid var(--line);background:var(--panel2);color:var(--txt);cursor:pointer;font-size:13px;font-weight:600}
.btn:hover{border-color:var(--acc)}
.btn.primary{background:linear-gradient(135deg,var(--acc),var(--acc2));border:none;color:#fff}
.btn.danger:hover{border-color:#e33;color:#ff8a8a}
.layout{display:flex;min-height:calc(100vh - 57px)}
.side{width:230px;background:var(--panel);border-right:1px solid var(--line);padding:18px;flex-shrink:0}
.side h4{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:1px;margin:14px 0 8px}
.side h4:first-child{margin-top:0}
.side .nav a{display:block;padding:9px 12px;border-radius:8px;font-size:14px;margin-bottom:2px}
.side .nav a:hover,.side .nav a.on{background:var(--panel2)}
.storage{background:var(--panel2);border-radius:12px;padding:14px;margin-top:18px}
.bar{height:8px;background:#111;border-radius:6px;overflow:hidden;margin:8px 0}
.bar i{display:block;height:100%;background:linear-gradient(90deg,var(--acc),#f0a35e);border-radius:6px}
.storage small{color:var(--mut);font-size:12px}
.main{flex:1;padding:20px;min-width:0}
.crumbs{display:flex;align-items:center;gap:6px;flex-wrap:wrap;margin-bottom:16px;font-size:14px}
.crumbs a{color:var(--acc);font-weight:600}
.crumbs a:hover{text-decoration:underline}
.toolbar{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:16px}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(130px,1fr));gap:12px}
.item{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:18px 10px;text-align:center;cursor:pointer;position:relative}
.item:hover{border-color:var(--acc)}
.item .ic{font-size:38px;margin-bottom:8px}
.item .nm{font-size:13px;word-break:break-all;max-height:36px;overflow:hidden}
.item .mt{font-size:11px;color:var(--mut);margin-top:4px}
.item .acts{position:absolute;top:6px;right:6px;display:none;gap:4px}
.item:hover .acts{display:flex}
.iconbtn{background:var(--panel2);border:1px solid var(--line);border-radius:6px;color:#fff;width:26px;height:26px;cursor:pointer;font-size:13px;line-height:1}
.iconbtn:hover{border-color:var(--acc)}
table{width:100%;border-collapse:collapse;background:var(--panel);border-radius:12px;overflow:hidden}
th,td{padding:11px 14px;text-align:left;font-size:13px;border-bottom:1px solid var(--line)}
th{color:var(--mut);font-size:11px;text-transform:uppercase;letter-spacing:.5px}
tr:hover td{background:var(--panel2)}
.drop{border:2px dashed #444;border-radius:14px;padding:26px;text-align:center;color:var(--mut);margin-bottom:16px;transition:.2s}
.drop.over{border-color:var(--acc);background:#2a1c14;color:#fff}
.prog{height:6px;background:#111;border-radius:4px;overflow:hidden;margin-top:10px;display:none}
.prog i{display:block;height:100%;width:0;background:var(--acc)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.75);display:none;align-items:center;justify-content:center;z-index:50;padding:16px}
.modal.open{display:flex}
.box{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:22px;width:100%;max-width:420px}
.box h3{margin-bottom:14px}
.box input{width:100%;padding:11px;background:var(--bg);border:1px solid var(--line);border-radius:10px;color:#fff;margin-bottom:12px;outline:none}
.box .row{display:flex;gap:8px;justify-content:flex-end}
.preview{max-width:90vw;max-height:80vh;border-radius:12px}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:#222;border:1px solid var(--acc);padding:12px 20px;border-radius:10px;display:none;z-index:99;font-size:14px}
@media(max-width:760px){.side{display:none}.main{padding:14px}}
</style></head><body>
<header class="top">
<div class="brand">☁️ My<span>Cloud</span></div>
<div class="search"><input id="q" placeholder="🔍 Files search karo..." oninput="render()"></div>
<button class="btn" onclick="toggleView()" id="viewBtn">☰ List</button>
<form method="POST" action="/logout" style="margin-left:auto"><button class="btn danger">🔒 Logout</button></form>
</header>
<div class="layout">
<aside class="side">
<div class="nav">
<a href="#" class="on" onclick="go('');return false">📁 Meri Files</a>
<a href="#" onclick="filterType('image');return false">🖼️ Photos</a>
<a href="#" onclick="filterType('video');return false">🎬 Videos</a>
<a href="#" onclick="filterType('audio');return false">🎵 Music</a>
<a href="#" onclick="filterType('doc');return false">📄 Documents</a>
</div>
<div class="storage"><b>💾 Storage</b><div class="bar"><i id="storBar" style="width:0%"></i></div><small id="storTxt">Loading...</small></div>
<h4>Quick Actions</h4>
<button class="btn" style="width:100%;margin-bottom:8px" onclick="openModal('mkdirModal')">📁 Naya Folder</button>
<button class="btn primary" style="width:100%" onclick="document.getElementById('fileInput').click()">⬆️ Upload File</button>
<input type="file" id="fileInput" multiple style="display:none" onchange="uploadFiles(this.files)">
</aside>
<main class="main">
<div class="crumbs" id="crumbs"></div>
<div class="drop" id="drop">📤 Files yahan drag & drop karo — ya Upload button dabao<br><small>Multiple files ek saath chalengi</small><div class="prog" id="prog"><i></i></div></div>
<div class="toolbar">
<button class="btn" onclick="openModal('mkdirModal')">📁 New Folder</button>
<button class="btn primary" onclick="document.getElementById('fileInput').click()">⬆️ Upload</button>
<button class="btn" onclick="downloadZip()">⬇️ Is folder ka ZIP download</button>
</div>
<div id="content"></div>
</main>
</div>

<div class="modal" id="mkdirModal"><div class="box"><h3>📁 Naya Folder</h3>
<input id="folderName" placeholder="Folder ka naam"><div class="row"><button class="btn" onclick="closeModal('mkdirModal')">Cancel</button><button class="btn primary" onclick="mkdir()">Create</button></div></div></div>

<div class="modal" id="renameModal"><div class="box"><h3>✏️ Rename</h3>
<input id="renameInput"><div class="row"><button class="btn" onclick="closeModal('renameModal')">Cancel</button><button class="btn primary" onclick="doRename()">Rename</button></div></div></div>

<div class="modal" id="previewModal"><div class="box" style="max-width:860px;text-align:center"><h3 id="prevTitle"></h3><div id="prevBody"></div><div class="row" style="margin-top:14px"><button class="btn" onclick="closeModal('previewModal')">Band karo</button></div></div></div>

<div class="toast" id="toast"></div>

<script>
let curPath="", files=[], view="grid", typeFilter="", renameTarget="";
const $=id=>document.getElementById(id);
function toast(m){const t=$("toast");t.textContent=m;t.style.display="block";setTimeout(()=>t.style.display="none",2600)}
function openModal(id){$(id).classList.add("open")}
function closeModal(id){$(id).classList.remove("open")}
function esc(s){return s.replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function icon(f){if(f.is_dir)return"📁";const e=f.name.split(".").pop().toLowerCase();
 if(["jpg","jpeg","png","gif","webp","svg"].includes(e))return"🖼️";
 if(["mp4","mkv","webm","mov"].includes(e))return"🎬";
 if(["mp3","wav","ogg"].includes(e))return"🎵";
 if(["pdf"].includes(e))return"📕";
 if(["zip","rar","tar","gz"].includes(e))return"🗜️";
 if(["txt","md","py","js","html","css","json"].includes(e))return"📄";
 return"📦"}
function fmt(n){if(n<1024)return n+" B";for(const u of["KB","MB","GB","TB"]){if(n<1024||u==="TB")return n.toFixed(1)+" "+u;n/=1024}}
function go(p){curPath=p;typeFilter="";load()}
function filterType(t){typeFilter=t;curPath="";load()}
function toggleView(){view=view==="grid"?"list":"grid";$("viewBtn").textContent=view==="grid"?"☰ List":"▦ Grid";render()}
async function load(){
 const r=await fetch("/api/files?path="+encodeURIComponent(curPath));
 const d=await r.json(); files=d.files||[];
 $("storTxt").textContent=d.used+" used • "+d.count+" files";
 $("storBar").style.width=Math.min(100,(d.used_bytes/Math.max(1,d.disk_total))*100)+"%";
 renderCrumbs(); render();
}
function renderCrumbs(){
 const parts=curPath?curPath.split("/").filter(Boolean):[];
 let h=`<a href="#" onclick="go('');return false">🏠 Home</a>`;
 parts.forEach((p,i)=>{h+=` <span style="color:#666">›</span> <a href="#" onclick="go('${esc(parts.slice(0,i+1).join("/"))}');return false">${esc(p)}</a>`});
 $("crumbs").innerHTML=h;
}
function visible(){
 let v=files;
 if(typeFilter){v=v.filter(f=>{const e=f.name.split(".").pop().toLowerCase();
  if(typeFilter==="image")return["jpg","jpeg","png","gif","webp","svg"].includes(e);
  if(typeFilter==="video")return["mp4","mkv","webm","mov"].includes(e);
  if(typeFilter==="audio")return["mp3","wav","ogg"].includes(e);
  if(typeFilter==="doc")return["pdf","txt","md","doc","docx"].includes(e);return false})}
 const q=$("q").value.toLowerCase();
 if(q)v=v.filter(f=>f.name.toLowerCase().includes(q));
 return v;
}
function render(){
 const v=visible(), c=$("content");
 if(!v.length){c.innerHTML=`<div style="text-align:center;color:#777;padding:50px">📭 Koi file nahi hai<br><small>Upload karke shuru karo</small></div>`;return}
 if(view==="grid"){
  c.innerHTML=`<div class="grid">`+v.map(f=>`
   <div class="item" ondblclick="${f.is_dir?`go('${esc(f.path)}')`:`preview('${esc(f.path)}','${esc(f.name)}')`}">
    <div class="acts">
     ${f.is_dir?"":`<button class="iconbtn" title="Download" onclick="event.stopPropagation();dl('${esc(f.path)}')">⬇</button>`}
     <button class="iconbtn" title="Rename" onclick="event.stopPropagation();askRename('${esc(f.path)}','${esc(f.name)}')">✏️</button>
     <button class="iconbtn" title="Delete" onclick="event.stopPropagation();del('${esc(f.path)}','${esc(f.name)}')">🗑</button>
    </div>
    <div class="ic">${icon(f)}</div><div class="nm">${esc(f.name)}</div>
    <div class="mt">${f.is_dir?"Folder":fmt(f.size)}</div>
   </div>`).join("")+`</div>`;
 } else {
  c.innerHTML=`<table><tr><th>Naam</th><th>Size</th><th>Modified</th><th></th></tr>`+v.map(f=>`
   <tr><td>${icon(f)} <a href="#" onclick="${f.is_dir?`go('${esc(f.path)}')`:`preview('${esc(f.path)}','${esc(f.name)}')`};return false">${esc(f.name)}</a></td>
   <td>${f.is_dir?"—":fmt(f.size)}</td><td>${f.mtime}</td>
   <td style="white-space:nowrap">${f.is_dir?"":`<button class="iconbtn" onclick="dl('${esc(f.path)}')">⬇</button> `}
   <button class="iconbtn" onclick="askRename('${esc(f.path)}','${esc(f.name)}')">✏️</button>
   <button class="iconbtn" onclick="del('${esc(f.path)}','${esc(f.name)}')">🗑</button></td></tr>`).join("")+`</table>`;
 }
}
function dl(p){location.href="/api/download?path="+encodeURIComponent(p)}
function downloadZip(){location.href="/api/zip?path="+encodeURIComponent(curPath)}
async function del(p,n){if(!confirm(`"${n}" delete karna hai? Ye wapas nahi aayega!`))return;
 const r=await fetch("/api/delete",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:p})});
 toast(r.ok?"🗑 Deleted":"Error");load()}
function askRename(p,n){renameTarget=p;$("renameInput").value=n;openModal("renameModal")}
async function doRename(){const name=$("renameInput").value.trim();if(!name)return;
 const r=await fetch("/api/rename",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:renameTarget,name})});
 closeModal("renameModal");toast(r.ok?"✏️ Renamed":"Error");load()}
async function mkdir(){const name=$("folderName").value.trim();if(!name)return;
 const r=await fetch("/api/mkdir",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({path:curPath,name})});
 closeModal("mkdirModal");$("folderName").value="";toast(r.ok?"📁 Folder ban gaya":"Error");load()}
async function preview(p,n){
 const e=n.split(".").pop().toLowerCase();
 $("prevTitle").textContent=n;
 let h="";
 if(["jpg","jpeg","png","gif","webp","svg"].includes(e))h=`<img class="preview" src="/api/download?path=${encodeURIComponent(p)}">`;
 else if(["mp4","webm","mov"].includes(e))h=`<video class="preview" controls src="/api/download?path=${encodeURIComponent(p)}"></video>`;
 else if(["mp3","wav","ogg"].includes(e))h=`<audio controls src="/api/download?path=${encodeURIComponent(p)}" style="width:100%"></audio>`;
 else if(e==="pdf")h=`<iframe src="/api/download?path=${encodeURIComponent(p)}" style="width:100%;height:70vh;border:none;border-radius:8px"></iframe>`;
 else if(["txt","md","py","js","html","css","json","log"].includes(e)){const r=await fetch("/api/download?path="+encodeURIComponent(p));h=`<pre style="text-align:left;background:#111;padding:14px;border-radius:8px;overflow:auto;max-height:60vh;font-size:12px">${esc((await r.text()).slice(0,20000))}</pre>`}
 else h=`<p style="color:#999">Preview available nahi hai. <a href="/api/download?path=${encodeURIComponent(p)}" style="color:var(--acc)">Download karo ⬇</a></p>`;
 $("prevBody").innerHTML=h;openModal("previewModal");
}
async function uploadFiles(list){
 if(!list.length)return;
 const prog=$("prog");prog.style.display="block";const bar=prog.querySelector("i");
 for(let i=0;i<list.length;i++){
  const f=list[i];bar.style.width=Math.round(((i+1)/list.length)*100)+"%";
  const fd=new FormData();fd.append("path",curPath);fd.append("file",f);
  const r=await fetch("/api/upload",{method:"POST",body:fd});
  if(!r.ok){toast("Upload fail: "+f.name);return}
 }
 prog.style.display="none";bar.style.width="0";$("fileInput").value="";
 toast("✅ Upload ho gaya!");load();
}
const drop=$("drop");
["dragover","dragenter"].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add("over")}));
["dragleave","drop"].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove("over")}));
drop.addEventListener("drop",ev=>uploadFiles(ev.dataTransfer.files));
document.querySelectorAll(".modal").forEach(m=>m.addEventListener("click",e=>{if(e.target===m)m.classList.remove("open")}));
load();
</script></body></html>"""


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
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">⏳ Zyada galat try! 60 sec ruko.</div>'), 429)
            return
        body = self.read_body().decode("utf-8", "replace")
        form = urllib.parse.parse_qs(body)
        password = (form.get("password") or [""])[0]
        cfg = load_config()
        if cfg is None:
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">⚠️ Server setup nahi hai. Pehle install karo.</div>'), 500)
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
            self.send_html(LOGIN_HTML.replace("%%ERR%%", '<div class="err">❌ Galat password. Dobara try karo.</div>'), 401)

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
        size = target.stat().st_size
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(size))
        self.send_header("Content-Disposition", f'attachment; filename="{target.name}"')
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
