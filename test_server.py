# -*- coding: utf-8 -*-
"""无界面功能自测：共享服务三种模式 + 控制台接口 + 二维码。"""
import io, os, sys, time, json, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bifrost_gui as bg

def http_get(url, raw=False):
    with urllib.request.urlopen(url, timeout=5) as r:
        data = r.read()
        return r.status, (data if raw else data.decode("utf-8", "replace"))

def http_get_json(url):
    s, text = http_get(url)
    return s, json.loads(text)

def http_post_json(url, obj):
    req = urllib.request.Request(url, data=json.dumps(obj).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))

def http_post_file(url, filename, content):
    boundary = b"----TESTBOUNDARY123"
    body = b"--" + boundary + b"\r\n"
    body += ('Content-Disposition: form-data; name="files"; filename="%s"\r\n' % filename).encode("utf-8")
    body += b"Content-Type: application/octet-stream\r\n\r\n"
    body += content + b"\r\n--" + boundary + b"--\r\n"
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "multipart/form-data; boundary=" + boundary.decode())
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, r.read().decode("utf-8", "replace")

# 准备测试数据
base = os.path.dirname(os.path.abspath(__file__))
tmp = os.path.join(base, "_testdata")
os.makedirs(tmp, exist_ok=True)
send_file = os.path.join(tmp, "你好_测试文件.txt")
open(send_file, "w", encoding="utf-8").write("Bifrost GUI 双向传输测试 ✓ 中文文件名")
browse_dir = os.path.join(tmp, "share"); os.makedirs(browse_dir, exist_ok=True)
for n in ["a.txt", "照片.jpg", "数据.csv"]:
    open(os.path.join(browse_dir, n), "w", encoding="utf-8").write("content of " + n)
recv_dir = os.path.join(tmp, "recv"); os.makedirs(recv_dir, exist_ok=True)

SHARE_PORT = 8899
CTRL_PORT = 8898
IP = "127.0.0.1"

# 起共享服务（直接操作 SHARE 单例，模拟控制台行为）
print("== 模式1：发送文件（双向） ==")
cfg = {"mode": "send", "port": SHARE_PORT, "file_path": send_file,
       "file_name": os.path.basename(send_file), "file_size": os.path.getsize(send_file),
       "output_dir": recv_dir}
bg.SHARE.start(cfg); time.sleep(0.4)
s, html = http_get("http://%s:%d/" % (IP, SHARE_PORT))
print("  GET / ->", s, "下载卡片:", "下载到手机" in html, "上传区:", "上传到电脑" in html)
s, data = http_get("http://%s:%d/download/%s" % (IP, SHARE_PORT, urllib.parse.quote(cfg["file_name"])), raw=True)
print("  GET /download ->", s, "内容匹配:", data.decode("utf-8").startswith("Bifrost GUI"))
s, resp = http_post_file("http://%s:%d/upload" % (IP, SHARE_PORT), "回传_手机.txt", "来自手机的内容".encode("utf-8"))
print("  POST /upload ->", s, resp)
bg.SHARE.stop(); time.sleep(0.2)

print("== 模式2：仅接收 ==")
bg.SHARE.start({"mode": "receive", "port": SHARE_PORT, "output_dir": recv_dir}); time.sleep(0.4)
s, html = http_get("http://%s:%d/" % (IP, SHARE_PORT))
print("  GET / ->", s, "上传区:", "上传到电脑" in html, "无下载卡片:", "下载到手机" not in html)
s, resp = http_post_file("http://%s:%d/upload" % (IP, SHARE_PORT), "仅接收_照片.png", b"\x89PNGfake")
print("  POST /upload ->", s, resp)
bg.SHARE.stop(); time.sleep(0.2)

print("== 模式3：浏览文件夹 ==")
bg.SHARE.start({"mode": "browse", "port": SHARE_PORT, "dir_path": browse_dir, "output_dir": browse_dir}); time.sleep(0.4)
s, html = http_get("http://%s:%d/" % (IP, SHARE_PORT))
print("  GET / ->", s, "列出全部:", all(x in html for x in ["a.txt", "照片.jpg", "数据.csv"]))
s, data = http_get("http://%s:%d/download/%s" % (IP, SHARE_PORT, urllib.parse.quote("照片.jpg")), raw=True)
print("  GET /download 照片.jpg ->", s, "内容匹配:", data.decode("utf-8").startswith("content of"))
bg.SHARE.stop(); time.sleep(0.2)

# 起控制台服务
print("== 控制台接口 ==")
from http.server import ThreadingHTTPServer
ctrl = ThreadingHTTPServer(("127.0.0.1", CTRL_PORT), bg.ControlHandler)
ctrl.daemon_threads = True
import threading
threading.Thread(target=ctrl.serve_forever, daemon=True).start()
time.sleep(0.4)

s, html = http_get("http://%s:%d/" % (IP, CTRL_PORT))
print("  GET / (控制台页) ->", s, "含标题:", "Bifrost" in html, "含三模式:", all(x in html for x in ["发送文件","仅接收","浏览共享文件夹"]))
s, st = http_get_json("http://%s:%d/api/state" % (IP, CTRL_PORT))
print("  GET /api/state ->", s, "running:", st["running"])
s, br = http_get_json("http://%s:%d/api/browse?path=" % (IP, CTRL_PORT))
print("  GET /api/browse (根) ->", s, "磁盘数:", len(br.get("drives", [])), "示例:", br.get("drives", [])[:3])
s, br2 = http_get_json("http://%s:%d/api/browse?path=%s" % (IP, CTRL_PORT, urllib.parse.quote(tmp)))
print("  GET /api/browse (_testdata) ->", s, "条目:", [i["name"] for i in br2["items"]])
s, cwd = http_get_json("http://%s:%d/api/cwd" % (IP, CTRL_PORT))
print("  GET /api/cwd ->", s, cwd["cwd"][:50])
s, qr = http_get("http://%s:%d/api/qr?text=%s" % (IP, CTRL_PORT, urllib.parse.quote("http://192.168.1.5:8888/")), raw=True)
print("  GET /api/qr ->", s, "是PNG:", qr[:4] == b"\x89PNG", "字节:", len(qr))

# 通过控制台 API 启动/停止共享
s, r = http_post_json("http://%s:%d/api/start" % (IP, CTRL_PORT),
                      {"mode": "browse", "path": browse_dir, "port": SHARE_PORT})
print("  POST /api/start (browse) ->", s, r)
s, st = http_get_json("http://%s:%d/api/state" % (IP, CTRL_PORT))
print("  GET /api/state -> running:", st["running"], "url:", st["url"])
s, r = http_post_json("http://%s:%d/api/stop" % (IP, CTRL_PORT), {})
print("  POST /api/stop ->", s, r)
s, st = http_get_json("http://%s:%d/api/state" % (IP, CTRL_PORT))
print("  GET /api/state -> running:", st["running"])

ctrl.shutdown(); ctrl.server_close()

print("== 落盘校验 ==")
for f in sorted(os.listdir(recv_dir)):
    print("  recv/", f)
print("\n✅ 全部测试完成")
