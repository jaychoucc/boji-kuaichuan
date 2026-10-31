# -*- coding: utf-8 -*-
"""
波吉快传 —— 图形化文件/消息桥（本地 Web 控制台版）
基于 axiom0x0/bifrost 的源码逻辑重做的图形化产品。

功能：
  1. 发送文件（双向）：共享某个文件，手机扫码下载，也可回传文件
  2. 仅接收文件：手机扫码上传文件到指定目录
  3. 浏览共享文件夹：共享整个文件夹，手机浏览/下载，也可回传
     （含“共享当前目录”快捷方式）
  4. 双向传输助手（类似微信文件传输助手）：电脑与手机互发文字和图片

无需 Go 环境，也无需 tkinter/Qt：在浏览器里打开控制台即可使用。
依赖：qrcode, Pillow
运行：python bifrost_gui.py   （自动打开控制台 http://127.0.0.1:8889/）
"""

import os
import re
import sys
import base64
import json
import socket
import shutil
import string
import threading
import time
import mimetypes
import urllib.parse
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import qrcode
from PIL import Image

APP_TITLE = "波吉快传"
DEFAULT_SHARE_PORT = 8888
# 控制台端口可用环境变量 BIFROST_CONTROL_PORT 覆盖（避免与其它实例冲突）
CONTROL_PORT = int(os.environ.get("BIFROST_CONTROL_PORT", "8889"))

# 应用图标（雪纳瑞小狗）。logo_data.py 由 make_icon.py 生成；打包时随 import 一并进入 exe。
try:
    from logo_data import LOGO_URI
except Exception:
    LOGO_URI = ""

LOGO_IMG = ('<img src="' + LOGO_URI + '" alt="logo" '
            'style="height:1.25em;vertical-align:-0.24em;border-radius:50%;">') if LOGO_URI else "🌈"
FAVICON = ('<link rel="icon" href="' + LOGO_URI + '">') if LOGO_URI else ""


def brand(html):
    """注入品牌：把 __LOGO_IMG__ 换成小狗图标，并在 </head> 前加 favicon。"""
    if LOGO_URI:
        html = html.replace("</head>", FAVICON + "</head>")
    return html.replace("__LOGO_IMG__", LOGO_IMG)

# ----------------------------------------------------------------------------
# 共享状态与日志
# ----------------------------------------------------------------------------

LOGS = deque(maxlen=300)
STATE = {
    "running": False,
    "mode": "",
    "url": "",
    "detail": "",
    "port": DEFAULT_SHARE_PORT,
}


def add_log(msg):
    LOGS.append(msg)


# ----------------------------------------------------------------------------
# 双向传输助手（聊天）状态 —— 电脑与手机互发文字/图片/文件
# 消息与附件本地保留：%APPDATA%\波吉快传\{chat_log.json, chat_files\}
# ----------------------------------------------------------------------------

_CHAT_LOCK = threading.Lock()
_CHAT_MSGS = []                        # [{id, sender, type:'rich', parts:[...], time}]
_CHAT_FILES = {}                       # key -> (bytes, mime, name)，图片与文件通用
_CHAT_SEQ = [0]                        # 自增 id
_CHAT_MAX = 200                        # 最多保留的消息数
_CHAT_IMG_MAX = 30 * 1024 * 1024       # 单张图片上限 30MB
_CHAT_FILE_MAX = 100 * 1024 * 1024     # 单个文件上限 100MB
_CHAT_LOADED = [False]                 # 是否已从磁盘加载过历史


def _chat_paths():
    """聊天数据目录。可用环境变量 BIFROST_DATA_DIR 覆盖（测试用）。"""
    base = os.environ.get("BIFROST_DATA_DIR") or os.environ.get("APPDATA") \
        or os.path.expanduser("~")
    d = os.path.join(base, "波吉快传")
    return os.path.join(d, "chat_log.json"), os.path.join(d, "chat_files")


def _chat_load():
    """从磁盘恢复聊天记录（幂等，首次访问时执行）。"""
    if _CHAT_LOADED[0]:
        return
    _CHAT_LOADED[0] = True
    try:
        logf, fdir = _chat_paths()
        if not os.path.isfile(logf):
            return
        with open(logf, "r", encoding="utf-8") as f:
            data = json.load(f)
        msgs = data.get("messages") or []
        seq = int(data.get("seq") or 0)
        with _CHAT_LOCK:
            _CHAT_MSGS[:] = msgs
            _CHAT_SEQ[0] = seq
            _CHAT_FILES.clear()
            for m in msgs:
                for p in m.get("parts") or []:
                    if isinstance(p, dict) and p.get("k") in ("img", "file"):
                        key = str(p.get("id"))
                        fp = os.path.join(fdir, key)
                        if os.path.isfile(fp):
                            try:
                                with open(fp, "rb") as f:
                                    raw = f.read()
                                name = str(p.get("name") or "文件")
                                mime = mimetypes.guess_type(name)[0] or "application/octet-stream"
                                if p.get("k") == "img" and not mime.startswith("image/"):
                                    mime = "image/png"
                                _CHAT_FILES[key] = (raw, mime, name)
                            except Exception:
                                pass
    except Exception:
        pass


def _chat_save():
    """把当前聊天记录与附件写到磁盘（每条新消息后调用；失败静默不影响使用）。"""
    try:
        logf, fdir = _chat_paths()
        os.makedirs(fdir, exist_ok=True)
        with _CHAT_LOCK:
            msgs = [dict(m) for m in _CHAT_MSGS]
            seq = _CHAT_SEQ[0]
            files = dict(_CHAT_FILES)
        with open(logf, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "seq": seq, "messages": msgs}, f, ensure_ascii=False)
        for key, (raw, _mime, _name) in files.items():
            fp = os.path.join(fdir, str(key))
            if not os.path.isfile(fp):
                with open(fp, "wb") as f:
                    f.write(raw)
        # 清理孤儿附件（消息已被裁剪掉的）
        valid = set()
        for m in msgs:
            for p in m.get("parts") or []:
                if isinstance(p, dict) and p.get("k") in ("img", "file"):
                    valid.add(str(p.get("id")))
        for fn in os.listdir(fdir):
            if fn not in valid:
                try:
                    os.remove(os.path.join(fdir, fn))
                except Exception:
                    pass
    except Exception:
        pass


def _chat_clear():
    """清空聊天记录（内存 + 磁盘）。"""
    with _CHAT_LOCK:
        _CHAT_MSGS[:] = []
        _CHAT_FILES.clear()
        _CHAT_SEQ[0] = 0
    try:
        logf, fdir = _chat_paths()
        os.makedirs(os.path.dirname(logf), exist_ok=True)
        with open(logf, "w", encoding="utf-8") as f:  # 覆盖为空记录（不依赖删除）
            json.dump({"version": 1, "seq": 0, "messages": []}, f, ensure_ascii=False)
    except Exception:
        pass
    try:
        for fn in os.listdir(fdir):
            try:
                os.remove(os.path.join(fdir, fn))
            except Exception:
                pass
    except Exception:
        pass


def _chat_next_id():
    with _CHAT_LOCK:
        _CHAT_SEQ[0] += 1
        return _CHAT_SEQ[0]


def _chat_add(msg):
    with _CHAT_LOCK:
        _CHAT_MSGS.append(msg)
        while len(_CHAT_MSGS) > _CHAT_MAX:
            old = _CHAT_MSGS.pop(0)
            # 清理该消息引用的附件
            for p in old.get("parts") or []:
                if isinstance(p, dict) and p.get("k") in ("img", "file"):
                    _CHAT_FILES.pop(p.get("id"), None)
    _chat_save()


def _chat_since(since):
    with _CHAT_LOCK:
        return [dict(m) for m in _CHAT_MSGS if m["id"] > since]


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------

def human_size(n):
    try:
        n = int(n)
    except Exception:
        return "0 B"
    if n < 1024:
        return "%d B" % n
    units = ["KB", "MB", "GB", "TB"]
    f = float(n)
    i = -1
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return "%.1f %s" % (f, units[i])


def file_icon(name):
    ext = os.path.splitext(name)[1].lower()
    table = {
        (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".bmp", ".heic"): "🖼",
        (".mp4", ".mov", ".avi", ".mkv", ".webm"): "🎬",
        (".mp3", ".wav", ".flac", ".aac", ".ogg", ".m4a"): "🎵",
        (".pdf",): "📕",
        (".doc", ".docx", ".txt", ".rtf", ".md"): "📄",
        (".xls", ".xlsx", ".csv"): "📊",
        (".zip", ".tar", ".gz", ".rar", ".7z"): "📦",
        (".gpx", ".kml", ".kmz"): "🗺",
    }
    for keys, icon in table.items():
        if ext in keys:
            return icon
    return "📎"


def get_local_ip():
    fallback = ""
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip.startswith("127."):
                continue
            if ip.startswith("192.168."):
                return ip
            if not fallback:
                fallback = ip
    except Exception:
        pass
    if fallback:
        return fallback
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def unique_path(directory, name):
    dest = os.path.join(directory, name)
    if not os.path.exists(dest):
        return dest
    base, ext = os.path.splitext(name)
    i = 1
    while True:
        dest = os.path.join(directory, "%s_%d%s" % (base, i, ext))
        if not os.path.exists(dest):
            return dest
        i += 1


def content_disposition(name):
    try:
        name.encode("ascii")
        return 'attachment; filename="%s"' % name
    except UnicodeEncodeError:
        return "attachment; filename*=UTF-8''%s" % urllib.parse.quote(name)


def list_drives():
    """列出磁盘盘符。优先用 GetLogicalDrives（只查询、不访问介质，避免空光驱/断连网络盘导致 isdir 卡死）。"""
    drives = []
    try:
        import ctypes
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for i, letter in enumerate(string.ascii_uppercase):
            if bitmask & (1 << i):
                drives.append(letter + ":\\")
        if drives:
            return drives
    except Exception:
        pass
    # 兜底（非 Windows 或 ctypes 失败）
    for letter in string.ascii_uppercase:
        d = letter + ":\\"
        try:
            if os.path.isdir(d):
                drives.append(d)
        except Exception:
            pass
    return drives


def get_downloads_dir():
    """返回系统“下载”目录的真实路径（即使用户把它重定向到了其它盘）。
    依次尝试：注册表 User Shell Folders → Shell Folders → ~/Downloads → 用户主目录。"""
    downloads_guid = "{374DE290-123F-4565-9164-39C4925E467B}"  # FOLDERID_Downloads
    for subkey in (r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
                   r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"):
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, subkey)
            val, _ = winreg.QueryValueEx(key, downloads_guid)
            winreg.CloseKey(key)
            path = os.path.expandvars(val)
            if os.path.isdir(path):
                return os.path.abspath(path)
        except Exception:
            pass
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    if os.path.isdir(d):
        return d
    return os.path.expanduser("~")


def get_documents_dir():
    """返回系统“文档”目录的真实路径（考虑重定向）。"""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders")
        val, _ = winreg.QueryValueEx(key, "Personal")  # Personal = Documents
        winreg.CloseKey(key)
        path = os.path.expandvars(val)
        if os.path.isdir(path):
            return os.path.abspath(path)
    except Exception:
        pass
    d = os.path.join(os.path.expanduser("~"), "Documents")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def get_default_receive_dir():
    """默认接收目录：文档\\下载（不存在则创建）。"""
    d = os.path.join(get_documents_dir(), "下载")
    try:
        os.makedirs(d, exist_ok=True)
    except Exception:
        pass
    return os.path.abspath(d)


# 知名文件夹物理名 → 中文显示名（与文件资源管理器一致）
LOCAL_NAMES = {
    "Desktop": "桌面", "Downloads": "下载", "Documents": "文档",
    "Pictures": "图片", "Music": "音乐", "Videos": "视频",
    "Favorites": "收藏夹", "Contacts": "联系人", "Links": "链接",
    "Searches": "搜索", "Saved Games": "保存的游戏",
}


def get_quick_folders():
    """文件选择器顶部的快捷跳转：下载/文档/桌面/图片。"""
    home = os.path.expanduser("~")
    candidates = [
        ("下载", get_downloads_dir()),
        ("文档", get_documents_dir()),
        ("桌面", os.path.join(home, "Desktop")),
        ("图片", os.path.join(home, "Pictures")),
    ]
    seen, out = set(), []
    for label, path in candidates:
        ap = os.path.abspath(path)
        if os.path.isdir(ap) and ap not in seen:
            seen.add(ap)
            out.append({"label": label, "path": ap})
    return out


# ----------------------------------------------------------------------------
# multipart/form-data 解析（Python 3.13 已移除 cgi 模块，手写解析）
# ----------------------------------------------------------------------------

def _decode_disposition_filename(header):
    m = re.search(r"filename\*\s*=\s*([^;]+)", header, re.IGNORECASE)
    if m:
        val = m.group(1).strip().strip('"')
        if "''" in val:
            val = val.split("''", 1)[1]
        try:
            return urllib.parse.unquote(val, encoding="utf-8", errors="replace")
        except Exception:
            return urllib.parse.unquote(val)
    m = re.search(r'filename\s*=\s*"([^"]*)"', header, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"filename\s*=\s*([^;]+)", header, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"')
    return None


def parse_multipart(body, boundary):
    files = []
    if not boundary:
        return files
    delim = b"--" + boundary
    for part in body.split(delim):
        if part in (b"", b"--", b"--\r\n", b"\r\n"):
            continue
        if part.startswith(b"--"):
            continue
        if part.startswith(b"\r\n"):
            part = part[2:]
        if part.endswith(b"\r\n"):
            part = part[:-2]
        if b"\r\n\r\n" not in part:
            continue
        head, content = part.split(b"\r\n\r\n", 1)
        try:
            header_text = head.decode("utf-8", "replace")
        except Exception:
            header_text = ""
        if "filename" not in header_text.lower():
            continue
        fname = _decode_disposition_filename(header_text)
        if not fname:
            continue
        fname = os.path.basename(fname)
        files.append((fname, content))
    return files


# ----------------------------------------------------------------------------
# 手机端页面（沿用 bifrost 原版视觉风格，非加密版）
# ----------------------------------------------------------------------------

_PAGE_CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, system-ui, sans-serif; background: #0d1117; color: #e6edf3; min-height: 100vh; }
.container { text-align: center; padding: 2rem; max-width: 420px; width: 100%; margin: 0 auto; }
.center { display:flex; align-items:center; justify-content:center; min-height:100vh; }
h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
.subtitle { color: #8b949e; margin-bottom: 1.5rem; font-size: 0.9rem; }
.section { margin-bottom: 2rem; }
.section-title { font-size: 1.1rem; margin-bottom: 0.75rem; color: #58a6ff; }
.download-card { background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 1.5rem; }
.download-card .filename { font-size: 1.1rem; font-weight: 600; word-break: break-all; }
.download-card .filesize { color: #8b949e; font-size: 0.85rem; margin-top: 0.25rem; }
.btn-download { display: inline-block; background: #1f6feb; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 1rem; cursor: pointer; width: 100%; margin-top: 1rem; text-decoration: none; text-align: center; }
.btn-download:hover { background: #388bfd; }
.upload-area { border: 2px dashed #30363d; border-radius: 12px; padding: 2.5rem 1.5rem; cursor: pointer; transition: border-color 0.2s; }
.upload-area:hover, .upload-area.drag { border-color: #58a6ff; }
.upload-area .icon { font-size: 2rem; }
.upload-area p { color: #8b949e; margin-top: 0.5rem; font-size: 0.9rem; }
input[type="file"] { display: none; }
.btn-upload { background: #238636; color: white; border: none; padding: 12px 24px; border-radius: 6px; font-size: 1rem; cursor: pointer; width: 100%; margin-top: 1rem; }
.btn-upload:disabled { background: #21262d; color: #484f58; cursor: not-allowed; }
.btn-upload:hover:not(:disabled) { background: #2ea043; }
.file-list { text-align: left; margin-top: 0.75rem; font-size: 0.85rem; color: #8b949e; }
.status { margin-top: 1rem; padding: 0.75rem; border-radius: 6px; display: none; font-size: 0.9rem; word-break: break-all; }
.status.success { display: block; background: #0d1f0d; border: 1px solid #238636; color: #3fb950; }
.status.error { display: block; background: #1f0d0d; border: 1px solid #da3633; color: #f85149; }
.divider { border: none; border-top: 1px solid #21262d; margin: 1.5rem 0; }
.progress-wrap { display: none; margin-top: 1rem; }
.progress-bar { width: 100%; height: 8px; background: #21262d; border-radius: 4px; overflow: hidden; }
.progress-fill { height: 100%; background: #58a6ff; border-radius: 4px; transition: width 0.2s; width: 0%; }
.progress-text { font-size: 0.8rem; color: #8b949e; margin-top: 0.25rem; text-align: right; }
.file-table { width: 100%; max-width: 600px; margin: 0 auto; }
.file-row { display: flex; align-items: center; padding: 0.75rem 1rem; border-bottom: 1px solid #21262d; text-decoration: none; color: #e6edf3; }
.file-row:hover { background: #161b22; }
.file-icon { font-size: 1.3rem; margin-right: 0.75rem; flex-shrink: 0; }
.file-info { flex: 1; min-width: 0; text-align: left; }
.file-name { font-weight: 500; word-break: break-all; }
.file-size { color: #8b949e; font-size: 0.8rem; margin-top: 0.15rem; }
.file-dl { color: #58a6ff; font-size: 0.85rem; flex-shrink: 0; margin-left: 0.75rem; }
.empty { text-align: center; color: #8b949e; padding: 3rem; }
.upload-section { max-width: 600px; margin: 2rem auto 0; text-align: center; }
"""

_UPLOAD_JS = """
function humanSize(b){ if(b<1024) return b+' B'; const u=['KB','MB','GB']; let i=-1; do{ b/=1024; i++; }while(b>=1024 && i<u.length-1); return b.toFixed(1)+' '+u[i]; }
function showProgress(fillId,textId,wrapId,loaded,total){ const w=document.getElementById(wrapId); const f=document.getElementById(fillId); const t=document.getElementById(textId); if(!w)return; w.style.display='block'; const pct= total>0? Math.round((loaded/total)*100):0; f.style.width=pct+'%'; t.textContent=humanSize(loaded)+' / '+humanSize(total)+' ('+pct+'%)'; }
const dz=document.getElementById('dropzone'); const fi=document.getElementById('files');
if(dz){ dz.addEventListener('dragover',e=>{e.preventDefault();dz.classList.add('drag');}); dz.addEventListener('dragleave',()=>dz.classList.remove('drag')); dz.addEventListener('drop',e=>{e.preventDefault();dz.classList.remove('drag'); fi.files=e.dataTransfer.files; showFiles(fi.files);}); }
function showFiles(files){ const list=document.getElementById('filelist'); list.innerHTML=Array.from(files).map(f=>'• '+f.name+' ('+humanSize(f.size)+')').join('<br>'); document.getElementById('submit').disabled = files.length===0; }
const form=document.getElementById('form');
if(form){ form.addEventListener('submit', e=>{ e.preventDefault(); const btn=document.getElementById('submit'); const status=document.getElementById('status'); btn.disabled=true; btn.textContent='上传中...'; status.style.display='none';
  const fd=new FormData(); for(const f of fi.files) fd.append('files', f);
  const xhr=new XMLHttpRequest(); xhr.open('POST','/upload'); xhr.upload.onprogress=function(ev){ if(ev.lengthComputable) showProgress('ulFill','ulText','ulProgress',ev.loaded,ev.total); };
  xhr.onload=function(){ status.className = xhr.status===200 ? 'status success':'status error'; status.textContent = xhr.responseText; btn.textContent='上传'; btn.disabled=false; if(xhr.status===200 && window.RELOAD_ON_UPLOAD){ setTimeout(()=>location.reload(),1500);} };
  xhr.onerror=function(){ status.className='status error'; status.textContent='上传失败：网络错误'; btn.textContent='上传'; btn.disabled=false; };
  xhr.send(fd); }); }
"""

_DOWNLOAD_JS = """
function trackDownload(e, fname){ e.preventDefault(); const btn=document.getElementById('downloadBtn'); btn.textContent='下载中...';
  const xhr=new XMLHttpRequest(); xhr.responseType='blob'; xhr.open('GET','/download/'+encodeURIComponent(fname));
  xhr.onprogress=function(ev){ if(ev.lengthComputable) showProgress('dlFill','dlText','dlProgress',ev.loaded,ev.total); };
  xhr.onload=function(){ if(xhr.status===200){ const url=URL.createObjectURL(xhr.response); const a=document.createElement('a'); a.href=url; a.download=fname; a.click(); URL.revokeObjectURL(url); btn.textContent='✓ 已下载'; } else { btn.textContent='下载失败'; } setTimeout(()=>btn.textContent='下载',2000); };
  xhr.onerror=function(){ btn.textContent='下载失败'; }; xhr.send(); }
"""


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _upload_section():
    return """
    <div class="section">
      <div class="section-title">⬆ 上传到电脑</div>
      <form id="form">
        <div class="upload-area" id="dropzone" onclick="document.getElementById('files').click()">
          <div class="icon">📁</div>
          点我选择文件
          <p>或拖拽到这里</p>
        </div>
        <input type="file" id="files" name="files" multiple onchange="showFiles(this.files)">
        <div class="file-list" id="filelist"></div>
        <button type="submit" class="btn-upload" id="submit" disabled>上传</button>
        <div class="progress-wrap" id="ulProgress">
          <div class="progress-bar"><div class="progress-fill" id="ulFill"></div></div>
          <div class="progress-text" id="ulText"></div>
        </div>
      </form>
      <div class="status" id="status"></div>
    </div>
    """


def build_send_page(hostname, file_name, file_size):
    return """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>波吉快传</title><style>%s</style></head><body>
<div class="center"><div class="container">
  <h1>__LOGO_IMG__波吉快传</h1><p class="subtitle">%s</p>
  <div class="section">
    <div class="section-title">⬇ 下载到手机</div>
    <div class="download-card">
      <div class="filename">%s</div>
      <div class="filesize">%s</div>
      <a href="#" class="btn-download" id="downloadBtn" onclick="trackDownload(event, '%s')">下载</a>
      <div class="progress-wrap" id="dlProgress">
        <div class="progress-bar"><div class="progress-fill" id="dlFill"></div></div>
        <div class="progress-text" id="dlText"></div>
      </div>
    </div>
  </div>
  <hr class="divider">
  %s
</div></div>
<script>window.RELOAD_ON_UPLOAD=false;</script>
<script>%s</script><script>%s</script>
</body></html>""" % (
        _PAGE_CSS, _esc(hostname),
        _esc(file_name), file_size, file_name.replace("\\", "\\\\").replace("'", "\\'"),
        _upload_section(), _UPLOAD_JS, _DOWNLOAD_JS,
    )


def build_receive_page(hostname):
    return """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>波吉快传</title><style>%s</style></head><body>
<div class="center"><div class="container">
  <h1>__LOGO_IMG__波吉快传</h1><p class="subtitle">%s</p>
  %s
</div></div>
<script>window.RELOAD_ON_UPLOAD=false;</script>
<script>%s</script>
</body></html>""" % (_PAGE_CSS, _esc(hostname), _upload_section(), _UPLOAD_JS)


def build_browse_page(hostname, directory, entries):
    rows = []
    for name, size in entries:
        href = "/download/" + urllib.parse.quote(name)
        rows.append(
            '<a class="file-row" href="%s">'
            '<span class="file-icon">%s</span>'
            '<div class="file-info"><div class="file-name">%s</div>'
            '<div class="file-size">%s</div></div>'
            '<span class="file-dl">⬇</span></a>'
            % (href, file_icon(name), _esc(name), size)
        )
    table = ('<div class="file-table">' + "".join(rows) + "</div>") if rows else '<div class="empty">此目录暂无文件</div>'
    return """<!DOCTYPE html><html><head><meta name="viewport" content="width=device-width, initial-scale=1">
<title>波吉快传 — 浏览</title><style>%s</style></head><body>
<div style="padding:1.5rem;">
  <div style="text-align:center;margin-bottom:1.5rem;">
    <h1>__LOGO_IMG__波吉快传</h1><p class="subtitle" style="margin-bottom:0;">%s — %s</p>
  </div>
  %s
  <div class="upload-section">
    <hr style="border:none;border-top:1px solid #21262d;margin-bottom:1.5rem;">
    %s
  </div>
</div>
<script>window.RELOAD_ON_UPLOAD=true;</script>
<script>%s</script>
</body></html>""" % (_PAGE_CSS, _esc(hostname), _esc(directory), table, _upload_section(), _UPLOAD_JS)


# 双向传输助手（聊天）页面 —— 富文本输入：文字/粘贴图片混排，深色主题
CHAT_PAGE = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1, user-scalable=no">
<title>__TITLE__ · 传输助手</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%}
body{font-family:-apple-system,"Segoe UI",system-ui,sans-serif;background:#0d1117;color:#e6edf3;display:flex;flex-direction:column;height:100vh}
header{padding:12px 14px;border-bottom:1px solid #30363d;background:#161b22;display:flex;align-items:center;gap:8px;flex-shrink:0}
header .t{font-size:16px;font-weight:600}
header .side{margin-left:auto;font-size:12px;color:#8b949e;background:#0d1117;border:1px solid #30363d;padding:3px 10px;border-radius:12px}
#msgs{flex:1;overflow-y:auto;padding:16px 14px;display:flex;flex-direction:column;gap:12px}
.row{display:flex;width:100%}
.row.mine{justify-content:flex-end}
.bubble{max-width:78%;padding:9px 13px;border-radius:14px;font-size:15px;line-height:1.5;word-break:break-word;white-space:pre-wrap}
.row.mine .bubble{background:#238636;color:#fff;border-bottom-right-radius:4px}
.row.other .bubble{background:#21262d;color:#e6edf3;border:1px solid #30363d;border-bottom-left-radius:4px}
.bubble img.msgimg{max-width:220px;max-height:220px;border-radius:10px;display:block;cursor:pointer;margin:3px 0}
.fcard{display:flex;align-items:center;gap:8px;background:rgba(0,0,0,.18);border:1px solid rgba(255,255,255,.22);border-radius:10px;padding:8px 12px;margin:3px 0;text-decoration:none;color:inherit;max-width:260px}
.row.other .fcard{background:#0d1117;border-color:#30363d}
.fcard:hover{border-color:#58a6ff}
.fcard .fn{flex:1;min-width:0;word-break:break-all;font-size:14px}
.fcard .fs{color:#8b949e;font-size:12px;flex-shrink:0}
.meta{font-size:10px;color:rgba(255,255,255,.7);margin-top:4px;text-align:right}
.row.other .meta{color:#6e7681}
.sys{text-align:center;font-size:12px;color:#6e7681}
#bar{display:flex;gap:8px;padding:10px;border-top:1px solid #30363d;background:#161b22;flex-shrink:0;align-items:flex-end}
#txt{flex:1;background:#0d1117;border:1px solid #30363d;border-radius:16px;color:#e6edf3;padding:10px 14px;font-size:15px;outline:none;max-height:150px;overflow-y:auto;min-height:42px;line-height:1.5;white-space:pre-wrap;word-break:break-word}
#txt:empty:before{content:attr(data-ph);color:#484f58}
#txt:focus{border-color:#58a6ff}
#txt img.thumb{max-height:64px;max-width:100px;border-radius:8px;vertical-align:middle;margin:2px 6px 2px 0;border:1px solid #30363d}
#txt .fchip{display:inline-block;background:#10233f;border:1px solid #2b4a77;border-radius:10px;padding:4px 10px;margin:2px 6px 2px 0;font-size:13px;white-space:nowrap;max-width:100%;overflow:hidden;text-overflow:ellipsis;vertical-align:middle}
.icobtn{background:#21262d;border:1px solid #30363d;color:#e6edf3;width:42px;height:42px;border-radius:50%;font-size:18px;cursor:pointer;display:flex;align-items:center;justify-content:center;flex-shrink:0}
#send{background:#238636;border-color:#238636;color:#fff;font-weight:600}
.clrbtn{margin-left:auto;background:none;border:1px solid #30363d;border-radius:8px;color:#8b949e;font-size:13px;padding:4px 9px;cursor:pointer}
.clrbtn:hover{color:#f85149;border-color:#da3633}
input[type=file]{display:none}
</style></head><body>
<header>__LOGO_IMG__<span class="t">__TITLE__ · 传输助手</span><span class="side" id="sideTag">…</span><button class="clrbtn" id="clearBtn" title="清空聊天记录">🗑 清空</button></header>
<div id="msgs"><div class="sys">已连接，可互发文字/图片/文件（电脑上支持 Ctrl+V 直接粘贴截图或文件）</div></div>
<div id="bar">
  <button class="icobtn" id="imgBtn" title="插入图片/文件">📎</button>
  <div id="txt" contenteditable="true" data-ph="输入文字，可直接粘贴图片或文件…"></div>
  <button class="icobtn" id="send" title="发送">➤</button>
  <input type="file" id="imgInput" multiple>
</div>
<script>
const SIDE = new URLSearchParams(location.search).get('side')==='pc' ? 'pc':'phone';
document.getElementById('sideTag').textContent = SIDE==='pc' ? '💻 电脑端' : '📱 手机端';
const msgs=document.getElementById('msgs'), txt=document.getElementById('txt'), imgInput=document.getElementById('imgInput');
let lastId=0, sending=false;
function esc(s){return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function humanSize(b){ if(!b) return ''; if(b<1024) return b+' B'; const u=['KB','MB','GB']; let i=-1; do{ b/=1024; i++; }while(b>=1024&&i<u.length-1); return b.toFixed(1)+' '+u[i]; }
function partsOf(m){ if(m.type==='rich') return m.parts||[]; if(m.type==='image') return [{k:'img',id:m.id}]; return [{k:'text',v:m.text}]; }
function addMsg(m){
  const row=document.createElement('div'); row.className='row '+(m.sender===SIDE?'mine':'other');
  let inner='';
  for(const p of partsOf(m)){
    if(p.k==='img') inner+='<img class="msgimg" src="/api/img?id='+encodeURIComponent(p.id)+'" onclick="window.open(this.src)">';
    else if(p.k==='file') inner+='<a class="fcard" href="/api/file?id='+encodeURIComponent(p.id)+'" download>📎 <span class="fn">'+esc(p.name||'文件')+'</span><span class="fs">'+humanSize(p.size)+'</span></a>';
    else inner+=esc(p.v||'');
  }
  row.innerHTML='<div class="bubble">'+inner+'<div class="meta">'+(m.time||'')+'</div></div>';
  msgs.appendChild(row);
}
async function poll(){
  try{
    const r=await fetch('/api/messages?since='+lastId); const d=await r.json();
    if(d.messages && d.messages.length){ d.messages.forEach(m=>{ addMsg(m); lastId=Math.max(lastId,m.id); }); msgs.scrollTop=msgs.scrollHeight; }
  }catch(e){}
}
// ---------- 富文本输入：粘贴/拖拽/选择 图片与文件 ----------
function placeCaretEnd(){ const r=document.createRange(); r.selectNodeContents(txt); r.collapse(false); const s=getSelection(); s.removeAllRanges(); s.addRange(r); }
function downscale(dataUrl, type){
  return new Promise(res=>{
    const im=new Image();
    im.onload=()=>{
      const MAX=1600, big=im.width>MAX||im.height>MAX||dataUrl.length>3145728;
      if(!big){ res(dataUrl); return; }
      const sc=Math.min(1, MAX/Math.max(im.width,im.height));
      const c=document.createElement('canvas'); c.width=Math.round(im.width*sc); c.height=Math.round(im.height*sc);
      c.getContext('2d').drawImage(im,0,0,c.width,c.height);
      let t=(type&&type.startsWith('image/'))?type:'image/png'; if(t==='image/svg+xml')t='image/png';
      res(c.toDataURL(t));
    };
    im.onerror=()=>res(dataUrl);
    im.src=dataUrl;
  });
}
function fileToDataUrl(file){
  return new Promise(res=>{ const fr=new FileReader(); fr.onload=()=>res(downscale(fr.result, file.type)); fr.readAsDataURL(file); });
}
function insertThumb(dataUrl, name){
  const img=document.createElement('img');
  img.src=dataUrl; img.className='thumb'; img.dataset.name=name||'图片.png';
  img.setAttribute('contenteditable','false');
  txt.appendChild(img); txt.appendChild(document.createTextNode('\\u00A0'));
  placeCaretEnd();
}
// 文件芯片：非图片文件以“📎 名称(大小)”占位，File 对象暂存 fileMap，发送时读取
const fileMap=new Map(); let fidSeq=0;
function insertFileChip(f){
  const fid='f'+(++fidSeq);
  fileMap.set(fid, f);
  const sp=document.createElement('span');
  sp.className='fchip'; sp.textContent='📎 '+(f.name||'文件')+' ('+humanSize(f.size)+')';
  sp.dataset.fid=fid; sp.dataset.name=f.name||'文件';
  sp.setAttribute('contenteditable','false');
  txt.appendChild(sp); txt.appendChild(document.createTextNode('\\u00A0'));
  placeCaretEnd();
}
function addFile(f){
  if(f.type && f.type.startsWith('image/')){ fileToDataUrl(f).then(d=>insertThumb(d, f.name||'截图.png')); }
  else { insertFileChip(f); }
}
txt.addEventListener('paste', e=>{
  e.preventDefault();
  const cd=e.clipboardData;
  if(!cd) return;
  // 1) 截图/复制的图片
  let hasImg=false;
  const items=cd.items||[];
  for(const it of items){
    if(it.kind==='file' && it.type && it.type.startsWith('image/')){
      const f=it.getAsFile();
      if(f){ hasImg=true; fileToDataUrl(f).then(d=>insertThumb(d, f.name||'截图.png')); }
    }
  }
  if(hasImg) return;
  // 2) 资源管理器复制的文件（电脑端 Ctrl+V 粘贴文件）
  if(cd.files && cd.files.length){
    let hasFile=false;
    for(const f of cd.files){ if(f && f.size>0){ addFile(f); hasFile=true; } }
    if(hasFile) return;
  }
  // 3) 纯文本
  const t=cd.getData('text/plain');
  if(t) document.execCommand('insertText', false, t);
});
txt.addEventListener('dragover', e=>e.preventDefault());
txt.addEventListener('drop', e=>{
  e.preventDefault();
  for(const f of (e.dataTransfer.files||[])){ addFile(f); }
});
imgInput.addEventListener('change', ()=>{
  for(const f of imgInput.files){ addFile(f); }
  imgInput.value='';
});
document.getElementById('imgBtn').onclick=()=>imgInput.click();
// ---------- 发送：解析输入框里的文字+图片+文件，按顺序混排成一条消息 ----------
function readB64(file){
  return new Promise(res=>{ const fr=new FileReader(); fr.onload=()=>res(String(fr.result).split(',')[1]||''); fr.onerror=()=>res(''); fr.readAsDataURL(file); });
}
async function buildParts(){
  const parts=[]; let buf='';
  const flush=()=>{ if(buf){ parts.push({k:'text',v:buf}); buf=''; } };
  (function walk(node){
    if(node.nodeType===3){ buf+=node.nodeValue; return; }
    if(node.tagName==='IMG'){ flush(); parts.push({k:'img',name:node.dataset.name||'图片.png',data:(node.src||'').split(',')[1]||''}); return; }
    if(node.tagName==='SPAN' && node.classList && node.classList.contains('fchip')){ flush(); parts.push({k:'file',name:node.dataset.name||'文件',_fid:node.dataset.fid}); return; }
    if(node.tagName==='BR'){ buf+='\\n'; return; }
    for(const c of node.childNodes) walk(c);
    if(node.tagName==='DIV'||node.tagName==='P') buf+='\\n';
  })(txt);
  flush();
  // 文件芯片：异步读取暂存的 File 对象为 base64（保持原顺序）
  for(const p of parts){
    if(p.k==='file' && p._fid){
      const f=fileMap.get(p._fid);
      p.data = f ? await readB64(f) : '';
      delete p._fid;
    }
  }
  return parts.filter(p=>(p.k==='img'||p.k==='file') ? (p.data&&p.data.length>16) : (p.v&&p.v.trim()));
}
async function sendRich(){
  if(sending) return;
  sending=true;
  let parts=[];
  try{ parts=await buildParts(); }catch(e){ sending=false; return; }
  if(!parts.length){ sending=false; return; }
  txt.innerHTML='';
  fileMap.clear();
  try{
    await fetch('/api/send?side='+SIDE,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({parts:parts})});
  }finally{ sending=false; }
  poll();
}
document.getElementById('send').onclick=sendRich;
txt.addEventListener('keydown',e=>{ if(e.key==='Enter'&&!e.shiftKey){ e.preventDefault(); sendRich(); } });
document.getElementById('clearBtn').onclick=async()=>{
  if(!confirm('确定清空全部聊天记录（含本地保存的历史）？')) return;
  try{ await fetch('/api/clear',{method:'POST'}); }catch(e){}
  location.reload();
};
setInterval(poll,1200); poll();
</script></body></html>"""


def build_chat_page():
    return CHAT_PAGE.replace("__TITLE__", APP_TITLE)


def error_page(title, code):
    return ("<!DOCTYPE html><html><head><meta name='viewport' content='width=device-width,initial-scale=1'>"
            "<style>body{background:#1a1a2e;color:#e0e0e0;font-family:system-ui;display:flex;justify-content:center;"
            "align-items:center;min-height:100vh;margin:0}.err{text-align:center}.err h1{font-size:4em;margin:0;color:#e94560}"
            ".err p{font-size:1.2em;color:#999}</style></head><body><div class='err'><h1>%d</h1><p>%s</p></div></body></html>"
            % (code, title))


# ----------------------------------------------------------------------------
# 共享服务（手机端访问）
# ----------------------------------------------------------------------------

class ShareHandler(BaseHTTPRequestHandler):
    server_version = "BifrostShare/1.0"

    def log_message(self, fmt, *args):
        pass

    @property
    def cfg(self):
        return self.server.cfg

    def _send_html(self, html, code=200):
        html = brand(html)
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, title, code):
        data = error_page(title, code).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _read_body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)
        if path == "/":
            self._serve_index()
        elif path.startswith("/download/"):
            name = urllib.parse.unquote(path[len("/download/"):])
            self._serve_download(name)
        elif path == "/api/messages":
            self._chat_messages(qs)
        elif path == "/api/img":
            self._chat_image(qs)
        elif path == "/api/file":
            self._chat_file(qs)
        else:
            self._send_error("Not Found", 404)

    def _serve_index(self):
        cfg = self.cfg
        host = socket.gethostname()
        if cfg["mode"] == "chat":
            self._send_html(build_chat_page())
        elif cfg["mode"] == "browse":
            entries = self._list_dir(cfg["dir_path"])
            label = os.path.basename(cfg["dir_path"].rstrip("\\/")) or cfg["dir_path"]
            self._send_html(build_browse_page(host, label, entries))
        elif cfg["mode"] == "receive":
            self._send_html(build_receive_page(host))
        else:
            self._send_html(build_send_page(host, cfg["file_name"], human_size(cfg["file_size"])))

    # ---------------- 双向传输助手（聊天） ----------------
    def _chat_messages(self, qs):
        _chat_load()  # 首次访问时从磁盘恢复历史
        try:
            since = int(qs.get("since", ["0"])[0])
        except (ValueError, TypeError):
            since = 0
        self._json({"messages": _chat_since(since)})

    def _chat_image(self, qs):
        _chat_load()
        key = qs.get("id", [""])[0]
        item = _CHAT_FILES.get(key)
        if not item:
            self._send_error("Not Found", 404)
            return
        data, mime, _name = item
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _chat_file(self, qs):
        """聊天中的文件下载。"""
        _chat_load()
        key = qs.get("id", [""])[0]
        item = _CHAT_FILES.get(key)
        if not item:
            self._send_error("Not Found", 404)
            return
        data, mime, name = item
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Disposition", content_disposition(name))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _chat_send(self, qs):
        """富文本消息。JSON: {parts:[{k:'text',v:...},{k:'img'|'file',name:...,data:<base64>}, ...]}"""
        _chat_load()
        side = qs.get("side", ["phone"])[0]
        if side not in ("pc", "phone"):
            side = "phone"
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        if length > 160 * 1024 * 1024:  # 整包上限
            self._json({"ok": False, "error": "内容过大"}, 413)
            return
        try:
            payload = json.loads(self._read_body().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        parts_in = payload.get("parts") or []
        if not isinstance(parts_in, list) or not parts_in:
            self._json({"ok": False, "error": "内容为空"}, 400)
            return
        now = time.strftime("%H:%M")
        mid = _chat_next_id()
        parts = []
        att_n = 0
        for i, p in enumerate(parts_in):
            if not isinstance(p, dict):
                continue
            k = p.get("k")
            if k == "text":
                v = p.get("v") or ""
                if v.strip():
                    parts.append({"k": "text", "v": v})
            elif k in ("img", "file") and att_n < 10:  # 单条消息最多 10 个附件
                cap = _CHAT_IMG_MAX if k == "img" else _CHAT_FILE_MAX
                try:
                    raw = base64.b64decode(p.get("data") or "")
                except Exception:
                    continue
                if not raw or len(raw) > cap:
                    continue
                name = os.path.basename(str(p.get("name") or ("图片.png" if k == "img" else "文件")))
                mime = mimetypes.guess_type(name)[0]
                if k == "img":
                    if not mime or not mime.startswith("image/"):
                        mime = "image/png"
                else:
                    if not mime:
                        mime = "application/octet-stream"
                key = "%d-%d" % (mid, i)
                _CHAT_FILES[key] = (raw, mime, name)
                part = {"k": k, "id": key, "name": name}
                if k == "file":
                    part["size"] = len(raw)
                parts.append(part)
                att_n += 1
        if not parts:
            self._json({"ok": False, "error": "内容为空"}, 400)
            return
        _chat_add({"id": mid, "sender": side, "type": "rich", "parts": parts, "time": now})
        self._json({"ok": True, "id": mid})

    def _chat_clear_handler(self):
        _chat_clear()
        self._json({"ok": True})

    def _list_dir(self, directory):
        entries = []
        try:
            for name in os.listdir(directory):
                full = os.path.join(directory, name)
                if name.startswith(".") or not os.path.isfile(full):
                    continue
                try:
                    size = os.path.getsize(full)
                except OSError:
                    size = 0
                entries.append((name, human_size(size)))
        except OSError:
            pass
        entries.sort(key=lambda x: x[0].lower())
        return entries

    def _serve_download(self, name):
        cfg = self.cfg
        if cfg["mode"] == "send":
            if name != cfg["file_name"]:
                self._send_error("Not Found", 404)
                return
            path = cfg["file_path"]
        elif cfg["mode"] == "browse":
            if not name or "/" in name or "\\" in name or ".." in name:
                self._send_error("Not Found", 404)
                return
            path = os.path.join(cfg["dir_path"], name)
        else:
            self._send_error("Not Found", 404)
            return

        if not os.path.isfile(path):
            self._send_error("Not Found", 404)
            return

        ctype, _ = mimetypes.guess_type(name)
        if not ctype:
            ctype = "application/octet-stream"
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition", content_disposition(name))
        self.send_header("Content-Length", str(size))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)
        add_log("⬇ 手机下载了：%s（%s）" % (name, human_size(size)))

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        if path == "/api/send":
            self._chat_send(urllib.parse.parse_qs(u.query))
            return
        if path == "/api/clear":
            self._chat_clear_handler()
            return
        if path != "/upload":
            self._send_error("Not Found", 404)
            return
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            self._send_error("Bad Request", 400)
            return
        m = re.search(r"boundary=([^;]+)", ctype)
        boundary = m.group(1).strip().strip('"').encode("utf-8") if m else b""
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else b""
        files = parse_multipart(body, boundary)
        if not files:
            self.send_response(400)
            msg = "未收到文件".encode("utf-8")
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        out_dir = self.cfg["output_dir"]
        os.makedirs(out_dir, exist_ok=True)
        saved = []
        for fname, content in files:
            dest = unique_path(out_dir, fname)
            with open(dest, "wb") as f:
                f.write(content)
            saved_name = os.path.basename(dest)
            saved.append(saved_name)
            add_log("⬆ 手机回传了：%s（%s）→ %s" % (saved_name, human_size(len(content)), out_dir))

        text = "✓ 已接收 %d 个文件：%s" % (len(saved), "、".join(saved))
        data = text.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class ShareServer:
    def __init__(self):
        self.httpd = None
        self.thread = None

    def start(self, cfg):
        httpd = ThreadingHTTPServer(("0.0.0.0", cfg["port"]), ShareHandler)
        httpd.cfg = cfg
        httpd.daemon_threads = True
        self.httpd = httpd
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        if self.httpd:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass
        self.httpd = None
        self.thread = None

    @property
    def running(self):
        return self.httpd is not None


SHARE = ShareServer()


# ----------------------------------------------------------------------------
# 控制台页面（桌面图形界面，跑在浏览器里）
# ----------------------------------------------------------------------------

CONTROL_HTML = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root{ --bg:#0d1117; --panel:#161b22; --border:#30363d; --fg:#e6edf3; --sub:#8b949e; --accent:#58a6ff; --green:#238636; --green2:#2ea043; --red:#da3633; }
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"Segoe UI",system-ui,sans-serif;background:var(--bg);color:var(--fg);min-height:100vh;padding:18px}
h1{font-size:20px}
.tag{color:var(--sub);font-size:12px;margin-left:10px;font-weight:400}
.wrap{max-width:1020px;margin:0 auto}
.grid{display:grid;grid-template-columns:1.15fr 0.85fr;gap:16px;margin-top:14px}
@media(max-width:820px){.grid{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--border);border-radius:12px;padding:16px}
.card h2{font-size:13px;color:var(--accent);margin-bottom:12px;letter-spacing:.5px}
.modes{display:flex;flex-direction:column;gap:8px}
.mode{border:1px solid var(--border);border-radius:10px;padding:11px 12px;cursor:pointer;font-size:14px;transition:.15s;background:#0d1117}
.mode:hover{border-color:var(--accent)}
.mode.active{border-color:var(--accent);background:#101b2d;box-shadow:0 0 0 1px var(--accent) inset}
.mode b{display:block}
.mode small{color:var(--sub)}
label{display:block;font-size:12px;color:var(--sub);margin:12px 0 5px}
.row{display:flex;gap:8px}
input[type=text]{flex:1;background:#0d1117;border:1px solid var(--border);border-radius:8px;color:var(--fg);padding:9px 10px;font-size:13px;font-family:Consolas,monospace}
input[type=text]:focus{outline:none;border-color:var(--accent)}
.btn{background:#21262d;color:var(--fg);border:1px solid var(--border);border-radius:8px;padding:9px 12px;font-size:13px;cursor:pointer;white-space:nowrap}
.btn:hover{border-color:var(--accent)}
.btn.primary{background:var(--green);border-color:var(--green);color:#fff;font-weight:600}
.btn.primary:hover{background:var(--green2)}
.btn.danger{background:var(--red);border-color:var(--red);color:#fff;font-weight:600}
.btn.big{width:100%;padding:13px;font-size:15px;margin-top:14px}
.status{margin-top:12px;font-size:13px;padding:10px;border-radius:8px;background:#0d1117;border:1px solid var(--border);word-break:break-all}
.status.on{border-color:var(--green);color:#3fb950}
.status.off{color:var(--sub)}
.qrbox{background:#fff;border-radius:12px;padding:14px;display:flex;align-items:center;justify-content:center;min-height:268px}
.qrbox img{width:240px;height:240px;image-rendering:pixelated}
.qrbox .ph{color:#666;font-size:13px;text-align:center}
.urlrow{display:flex;gap:8px;margin-top:12px;align-items:center}
#url{flex:1;font-family:Consolas,monospace;color:var(--accent);font-size:13px;background:#0d1117;border:1px solid var(--border);border-radius:8px;padding:9px 10px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.log{margin-top:12px;background:#0d1117;border:1px solid var(--border);border-radius:8px;padding:10px;height:170px;overflow-y:auto;font-family:Consolas,monospace;font-size:12px;line-height:1.6}
.log div{white-space:pre-wrap;word-break:break-all}
.hint{font-size:12px;color:var(--sub);margin-top:10px}
/* modal */
.modal{position:fixed;inset:0;background:rgba(0,0,0,.6);display:none;align-items:center;justify-content:center;z-index:50}
.modal.open{display:flex}
.mbox{background:var(--panel);border:1px solid var(--border);border-radius:12px;width:min(560px,92vw);max-height:80vh;display:flex;flex-direction:column;overflow:hidden}
.mhead{padding:12px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px}
.mhead .path{flex:1;font-family:Consolas,monospace;font-size:12px;color:var(--accent);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mlist{overflow-y:auto;padding:6px}
.item{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:8px;cursor:pointer;font-size:14px}
.item:hover{background:#101b2d}
.item .nm{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.item .sz{color:var(--sub);font-size:12px}
.item.sel{background:#10233f;outline:1px solid var(--accent)}
.mfoot{padding:12px 14px;border-top:1px solid var(--border);display:flex;gap:8px;justify-content:flex-end}
.drives{display:flex;gap:6px;flex-wrap:wrap;padding:8px 10px;border-bottom:1px solid var(--border)}
.drives .btn{padding:5px 10px}
</style></head><body>
<div class="wrap">
  <h1>__LOGO_IMG____TITLE__<span class="tag">手机扫码 · 文件/文字/图片互传 · 无需装 App</span></h1>
  <div class="grid">
    <div class="card">
      <h2>① 选择功能</h2>
      <div class="modes">
        <div class="mode" data-m="chat" onclick="setMode('chat')"><b>💬 双向传输助手</b><small>类似文件传输助手，电脑与手机互发文字和图片</small></div>
        <div class="mode" data-m="send" onclick="setMode('send')"><b>📤 发送文件（双向）</b><small>共享某个文件，手机扫码下载，也可回传文件</small></div>
        <div class="mode" data-m="receive" onclick="setMode('receive')"><b>📥 仅接收文件</b><small>手机扫码，把照片/文件上传到电脑</small></div>
        <div class="mode" data-m="browse" onclick="setMode('browse')"><b>📁 浏览共享文件夹</b><small>共享整个文件夹，手机浏览/下载，也可回传</small></div>
      </div>

      <div id="cfg-send" class="cfg">
        <label>要共享的文件</label>
        <div class="row"><input type=text id="send-path" placeholder="点击右侧按钮选择文件…" readonly><button class="btn" onclick="openPicker('file','send-path')">选择文件…</button></div>
        <label>回传文件保存到</label>
        <div class="row"><input type=text id="send-out" readonly><button class="btn" onclick="openPicker('dir','send-out')">选择目录…</button></div>
      </div>

      <div id="cfg-receive" class="cfg" style="display:none">
        <label>手机上传的文件保存到</label>
        <div class="row"><input type=text id="recv-out" readonly><button class="btn" onclick="openPicker('dir','recv-out')">选择目录…</button></div>
      </div>

      <div id="cfg-browse" class="cfg" style="display:none">
        <label>要共享的文件夹</label>
        <div class="row"><input type=text id="browse-path" readonly><button class="btn" onclick="openPicker('dir','browse-path')">选择文件夹…</button></div>
        <div class="row" style="margin-top:6px"><button class="btn" onclick="useCwd()">📂 共享当前目录</button></div>
      </div>

      <div id="cfg-chat" class="cfg" style="display:none">
        <label>双向传输助手（富文本）</label>
        <div class="hint" style="margin-top:0">无需选文件。启动后：<b>电脑</b>在右侧窗口操作，<b>手机</b>扫码加入，双方即可互发文字、图片和文件。电脑上支持 <b>Ctrl+V 直接粘贴截图或文件</b>（资源管理器里复制什么都能粘），也可拖拽进输入框；混排成一条消息发送。<b>聊天记录自动本地保留</b>，重启不丢（聊天页右上角可清空）。</div>
      </div>

      <label>共享端口</label>
      <div class="row"><input type=text id="port" value="__PORT__" style="max-width:120px"></div>

      <button id="toggle" class="btn primary big" onclick="toggle()">▶ 启动共享</button>
      <div id="status" class="status off">未启动</div>
    </div>

    <div class="card">
      <h2>② 手机扫码连接</h2>
      <div class="qrbox" id="qrbox"><div class="ph">启动后<br>这里会显示二维码</div></div>
      <div class="urlrow"><div id="url">—</div><button class="btn" onclick="copyUrl()">复制链接</button></div>
      <div class="hint">提示：手机需与电脑连接<b>同一局域网（同一 WiFi）</b>。扫码后即可互传。</div>
      <iframe id="chatframe" title="chat" style="display:none;width:100%;height:460px;border:1px solid var(--border);border-radius:12px;background:#0d1117;margin-top:12px"></iframe>
      <h2 style="margin-top:14px" id="loghead">传输记录</h2>
      <div class="log" id="log"></div>
    </div>
  </div>
</div>

<div class="modal" id="modal">
  <div class="mbox">
    <div class="mhead"><button class="btn" onclick="pickerUp()">⬆ 上级</button><div class="path" id="p-path"></div><button class="btn" onclick="closePicker()">✕</button></div>
    <div class="drives" id="p-quick"></div>
    <div class="drives" id="p-drives"></div>
    <div class="mlist" id="p-list"></div>
    <div class="mfoot">
      <button class="btn" onclick="pickerSelectCurrent()" id="p-self">选择当前文件夹</button>
      <button class="btn primary" onclick="pickerConfirm()" id="p-ok" disabled>确定</button>
    </div>
  </div>
</div>

<script>
let mode='send';
const $=id=>document.getElementById(id);
const DEFAULT_DIR='__DEFAULT_DIR__';
function setMode(m){ mode=m; document.querySelectorAll('.mode').forEach(e=>e.classList.toggle('active',e.dataset.m===m));
  ['send','receive','browse','chat'].forEach(x=>$('cfg-'+x).style.display = x===m?'block':'none'); }
setMode('send');
// 默认保存到系统“下载”目录
$('send-out').value = DEFAULT_DIR;
$('recv-out').value = DEFAULT_DIR;

// ---- 文件选择器 ----
let pickerType='dir', pickerTarget='', pickerPath='', pickerSel='';
function useCwd(){ fetch('/api/cwd').then(r=>r.json()).then(d=>{ $('browse-path').value=d.cwd; }); }
function openPicker(type,target){ pickerType=type; pickerTarget=target; pickerSel='';
  $('p-self').style.display = type==='dir' ? 'inline-block':'none';
  const cur=( $(target).value || '' ).trim();
  loadPicker(cur || DEFAULT_DIR); $('modal').classList.add('open'); }
function closePicker(){ $('modal').classList.remove('open'); }
function syncOk(){ $('p-ok').disabled = (pickerType==='file' && !pickerSel); }
function pickerUp(){ if(pickerPath){ const p=pickerPath.replace(/[\\\\/]+$/,''); const i=Math.max(p.lastIndexOf('/'),p.lastIndexOf('\\\\'));
   loadPicker(i>0? p.substring(0,i+1) : ''); } }
function pickerSelectCurrent(){ if(pickerPath){ $(pickerTarget).value=pickerPath; closePicker(); } }
function pickerConfirm(){ const v = pickerSel || pickerPath; if(v){ $(pickerTarget).value=v; closePicker(); } }
function pickItem(div,it){ pickerSel=it.full; document.querySelectorAll('.item').forEach(x=>x.classList.remove('sel')); div.classList.add('sel'); syncOk(); }
function loadPicker(path){ fetch('/api/browse?path='+encodeURIComponent(path)).then(r=>r.json()).then(d=>{
  pickerPath=d.path; pickerSel=''; syncOk(); $('p-path').textContent=d.path||'选择磁盘';
  const qk=$('p-quick'); qk.innerHTML=''; (d.quick||[]).forEach(q=>{ const b=document.createElement('button'); b.className='btn'; b.textContent='⭐ '+q.label; b.onclick=()=>loadPicker(q.path); qk.appendChild(b); });
  const dv=$('p-drives'); dv.innerHTML=''; (d.drives||[]).forEach(dr=>{ const b=document.createElement('button'); b.className='btn'; b.textContent=dr; b.onclick=()=>loadPicker(dr); dv.appendChild(b); });
  const list=$('p-list'); list.innerHTML='';
  d.items.forEach(it=>{ const div=document.createElement('div'); div.className='item';
    const disp = it.disp || it.name;
    div.innerHTML='<span>'+(it.dir?'📁':icon(it.name))+'</span><span class="nm">'+disp+'</span><span class="sz">'+(it.dir?'':it.size)+'</span>';
    if(it.dir){
      if(pickerType==='dir'){ div.onclick=()=>pickItem(div,it); div.ondblclick=()=>loadPicker(it.full); div.title='单击选中此文件夹，双击进入'; }
      else { div.onclick=()=>loadPicker(it.full); }
    } else if(pickerType==='file'){ div.onclick=()=>pickItem(div,it); }
    list.appendChild(div); });
  if(!d.items.length) list.innerHTML='<div style="padding:20px;color:#8b949e;text-align:center">（空目录）<br>选文件夹的话直接点下方“确定”即可</div>';
}); }
function icon(n){ const e=(n.split('.').pop()||'').toLowerCase();
  if(['jpg','jpeg','png','gif','webp','svg','bmp','heic'].includes(e))return'🖼';
  if(['mp4','mov','avi','mkv','webm'].includes(e))return'🎬';
  if(['mp3','wav','flac','aac','ogg','m4a'].includes(e))return'🎵';
  if(e==='pdf')return'📕'; if(['doc','docx','txt','rtf','md'].includes(e))return'📄';
  if(['xls','xlsx','csv'].includes(e))return'📊'; if(['zip','tar','gz','rar','7z'].includes(e))return'📦'; return'📎'; }

// ---- 启停 ----
function toggle(){ fetch('/api/state').then(r=>r.json()).then(s=>{ s.running? doStop(): doStart(); }); }
function doStart(){
  const port=parseInt($('port').value)||__PORT__;
  let body={mode:mode, port:port};
  if(mode==='send'){ body.path=$('send-path').value; body.output=$('send-out').value; }
  if(mode==='receive'){ body.output=$('recv-out').value; }
  if(mode==='browse'){ body.path=$('browse-path').value; }
  fetch('/api/start',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
   .then(r=>r.json()).then(d=>{ if(!d.ok) alert(d.error||'启动失败'); refresh(); });
}
function doStop(){ fetch('/api/stop',{method:'POST'}).then(()=>refresh()); }
function copyUrl(){ const t=$('url').textContent; if(t&&t!=='—'){ navigator.clipboard.writeText(t); } }

// ---- 状态轮询 ----
let lastLog=0;
function refresh(){ fetch('/api/state').then(r=>r.json()).then(s=>{
  const st=$('status'), tg=$('toggle'), cf=$('chatframe');
  if(s.running){ st.className='status on'; st.innerHTML='● 运行中 ['+modeName(s.mode)+'] '+s.detail+'<br>地址 '+s.url;
    tg.textContent='⏹ 停止共享'; tg.className='btn danger big'; $('url').textContent=s.url;
    $('qrbox').innerHTML='<img src="/api/qr?text='+encodeURIComponent(s.url)+'&t='+Date.now()+'">';
  } else { st.className='status off'; st.textContent='未启动'; tg.textContent='▶ 启动共享'; tg.className='btn primary big';
    $('url').textContent='—'; $('qrbox').innerHTML='<div class="ph">启动后<br>这里会显示二维码</div>'; }
  // 聊天模式：显示内嵌聊天窗口（电脑端 side=pc），隐藏传输记录
  const isChat = s.running && s.mode==='chat';
  cf.style.display = isChat ? 'block':'none';
  $('loghead').style.display = isChat ? 'none':'block';
  $('log').style.display = isChat ? 'none':'block';
  if(isChat){ const src='http://127.0.0.1:'+s.port+'/?side=pc'; if(cf.getAttribute('src')!==src) cf.src=src; }
  else if(cf.getAttribute('src')){ cf.removeAttribute('src'); }
  const logs=s.logs||[]; if(logs.length!==lastLog){ lastLog=logs.length; const el=$('log'); el.innerHTML=logs.map(x=>'<div>'+esc(x)+'</div>').join(''); el.scrollTop=el.scrollHeight; }
}); }
function modeName(m){ return {chat:'双向传输助手',send:'发送文件',receive:'仅接收',browse:'浏览文件夹'}[m]||m; }
function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
setInterval(refresh,1500); refresh();
</script></body></html>"""


# ----------------------------------------------------------------------------
# 控制台服务
# ----------------------------------------------------------------------------

class ControlHandler(BaseHTTPRequestHandler):
    server_version = "BifrostControl/1.0"

    def log_message(self, fmt, *args):
        pass

    def _json(self, obj, code=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _html(self, html):
        html = brand(html)
        data = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
        except ValueError:
            n = 0
        return self.rfile.read(n) if n > 0 else b""

    # ---------------- GET ----------------
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        qs = urllib.parse.parse_qs(u.query)

        if path == "/":
            default_dir_js = get_default_receive_dir().replace("\\", "\\\\").replace("'", "\\'")
            html = (CONTROL_HTML
                    .replace("__TITLE__", APP_TITLE)
                    .replace("__PORT__", str(DEFAULT_SHARE_PORT))
                    .replace("__DEFAULT_DIR__", default_dir_js))
            self._html(html)
        elif path == "/api/state":
            self._json({
                "running": SHARE.running,
                "mode": STATE["mode"],
                "url": STATE["url"],
                "detail": STATE["detail"],
                "port": STATE["port"],
                "logs": list(LOGS),
            })
        elif path == "/api/cwd":
            self._json({"cwd": os.getcwd()})
        elif path == "/api/browse":
            self._api_browse(qs.get("path", [""])[0])
        elif path == "/api/qr":
            self._api_qr(qs.get("text", [""])[0])
        else:
            self._json({"error": "not found"}, 404)

    def _api_qr(self, text):
        if not text:
            text = "http://127.0.0.1"
        qr = qrcode.QRCode(border=2, box_size=10)
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white").convert("RGB")
        buf = __import__("io").BytesIO()
        img.save(buf, format="PNG")
        data = buf.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_browse(self, path):
        drives = list_drives()
        quick = get_quick_folders()
        if not path:
            items = [{"name": d, "disp": d, "dir": True, "full": d, "size": ""} for d in drives]
            self._json({"path": "", "drives": drives, "quick": quick, "items": items})
            return
        if not os.path.isdir(path):
            self._json({"path": path, "drives": drives, "quick": quick, "items": [], "error": "not a dir"})
            return
        items = []
        try:
            for name in sorted(os.listdir(path), key=lambda s: s.lower()):
                if name.startswith("."):
                    continue
                full = os.path.join(path, name)
                is_dir = os.path.isdir(full)
                size = ""
                if not is_dir:
                    try:
                        size = human_size(os.path.getsize(full))
                    except OSError:
                        size = ""
                disp = LOCAL_NAMES.get(name, name) if is_dir else name
                items.append({"name": name, "disp": disp, "dir": is_dir, "full": full, "size": size})
        except OSError:
            pass
        # 文件夹排前面
        items.sort(key=lambda x: (not x["dir"], x["name"].lower()))
        self._json({"path": path, "drives": drives, "quick": quick, "items": items})

    # ---------------- POST ----------------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/start":
            self._api_start()
        elif path == "/api/stop":
            SHARE.stop()
            STATE["running"] = False
            STATE["url"] = ""
            STATE["detail"] = ""
            add_log("■ 服务已停止")
            self._json({"ok": True})
        else:
            self._json({"error": "not found"}, 404)

    def _api_start(self):
        try:
            body = json.loads(self._body().decode("utf-8") or "{}")
        except Exception:
            self._json({"ok": False, "error": "请求格式错误"}, 400)
            return

        mode = body.get("mode", "send")
        try:
            port = int(body.get("port", DEFAULT_SHARE_PORT))
            if not (1 <= port <= 65535):
                raise ValueError
        except (ValueError, TypeError):
            self._json({"ok": False, "error": "端口无效（1–65535）"}, 400)
            return

        if SHARE.running:
            SHARE.stop()

        cfg = {"mode": mode, "port": port}
        default_out = get_default_receive_dir()
        out = (body.get("output") or "").strip() or default_out

        if mode == "send":
            fp = (body.get("path") or "").strip()
            if not fp or not os.path.isfile(fp):
                self._json({"ok": False, "error": "请先选择要共享的文件"}, 400)
                return
            fp = os.path.abspath(fp)
            cfg["file_path"] = fp
            cfg["file_name"] = os.path.basename(fp)
            cfg["file_size"] = os.path.getsize(fp)
            cfg["output_dir"] = os.path.abspath(out)
            detail = "文件：%s（%s）" % (cfg["file_name"], human_size(cfg["file_size"]))
        elif mode == "browse":
            dp = (body.get("path") or "").strip()
            if not dp or not os.path.isdir(dp):
                self._json({"ok": False, "error": "请先选择要共享的文件夹（或点“共享当前目录”）"}, 400)
                return
            dp = os.path.abspath(dp)
            cfg["dir_path"] = dp
            cfg["output_dir"] = dp
            detail = "文件夹：%s" % dp
        elif mode == "chat":
            # 双向传输助手：无需文件/目录，消息存内存
            detail = "双向文本/图片传输"
        else:  # receive
            out_abs = os.path.abspath(out)
            os.makedirs(out_abs, exist_ok=True)
            cfg["output_dir"] = out_abs
            detail = "保存到：%s" % out_abs

        ip = get_local_ip()
        cfg["ip"] = ip
        url = "http://%s:%d/" % (ip, port)
        if mode == "chat":
            url += "?side=phone"  # 手机扫码进入手机端

        try:
            SHARE.start(cfg)
        except OSError as e:
            self._json({"ok": False, "error": "无法启动：%s（端口 %d 可能被占用）" % (e, port)}, 500)
            return

        STATE.update({"running": True, "mode": mode, "url": url, "detail": detail, "port": port})
        mode_name = {"chat": "双向传输助手", "send": "发送文件", "receive": "仅接收", "browse": "浏览文件夹"}[mode]
        add_log("✔ 服务已启动（%s）— %s" % (mode_name, url))
        self._json({"ok": True, "url": url})


def main():
    # Windows 中文控制台默认 GBK，print emoji 会 UnicodeEncodeError 崩溃；
    # 改为 replace 错误处理（emoji 显示为占位符，但不至于闪退）。
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(errors="replace")
        except Exception:
            pass
    control = ThreadingHTTPServer(("127.0.0.1", CONTROL_PORT), ControlHandler)
    control.daemon_threads = True
    url = "http://127.0.0.1:%d/" % CONTROL_PORT
    print("=" * 56)
    print("  🌈 %s" % APP_TITLE)
    print("  控制台地址： %s" % url)
    print("  （浏览器会自动打开；没有的话请手动访问上面的地址）")
    print("  按 Ctrl+C 退出")
    print("=" * 56)
    try:
        webbrowser.open(url)
    except Exception:
        pass
    try:
        control.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        SHARE.stop()
        control.server_close()


if __name__ == "__main__":
    main()
