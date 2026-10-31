# -*- coding: utf-8 -*-
"""双向传输助手自测：富文本(文字/图片/文件) + 聊天记录本地保留 + 清空。"""
import os, sys, json, time, base64, threading, urllib.request, urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 独立数据目录，避免污染真实 %APPDATA%
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_chatdata")
os.environ["BIFROST_DATA_DIR"] = DATA_DIR

import bifrost_gui as bg

IP = "127.0.0.1"
PORT = 8894

def get(url, raw=False):
    with urllib.request.urlopen(url, timeout=5) as r:
        d = r.read()
        return r.status, (d if raw else d.decode("utf-8", "replace"))

def get_json(url):
    s, t = get(url); return s, json.loads(t)

def post_parts(side, parts):
    req = urllib.request.Request("http://%s:%d/api/send?side=%s" % (IP, PORT, side),
                                 data=json.dumps({"parts": parts}).encode("utf-8"),
                                 method="POST", headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"ok": False, "code": e.code}

def post(url):
    req = urllib.request.Request(url, data=b"{}", method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode("utf-8"))

IMG = b"\x89PNG\r\n\x1a\n fakeimagedata"
FILE = "这是一份测试文件的内容 hello 123".encode("utf-8")

bg.SHARE.start({"mode": "chat", "port": PORT})
time.sleep(0.4)

print("== 聊天页面 ==")
s, html = get("http://%s:%d/?side=pc" % (IP, PORT))
print("  GET / ->", s,
      "| contenteditable:", 'contenteditable="true"' in html,
      "| 粘贴文件JS:", "cd.files" in html,
      "| 文件芯片:", "fchip" in html,
      "| 文件卡片:", "fcard" in html,
      "| 清空按钮:", "clearBtn" in html,
      "| 文件下载JS:", "/api/file" in html)

print("== 富文本消息（文字+图片+文件混排） ==")
r1 = post_parts("pc", [
    {"k": "text", "v": "发你一个文件和一张图"},
    {"k": "img", "name": "截图.png", "data": base64.b64encode(IMG).decode()},
    {"k": "file", "name": "说明文档.txt", "data": base64.b64encode(FILE).decode()},
    {"k": "text", "v": "收一下"},
])
print("  混排消息:", r1)
r2 = post_parts("phone", [{"k": "file", "name": "报表.xlsx", "data": base64.b64encode(FILE * 3).decode()}])
print("  手机发文件:", r2)

print("== 拉取解析 ==")
s, d = get_json("http://%s:%d/api/messages?since=0" % (IP, PORT))
msgs = d["messages"]
for m in msgs:
    desc = " + ".join((p.get("v") or p.get("name")) for p in m.get("parts", []))
    print("   -", m["id"], m["sender"], "|", desc)
assert len(msgs) == 2
m1 = msgs[0]
assert [p["k"] for p in m1["parts"]] == ["text", "img", "file", "text"], "parts顺序应保持"

print("== 图片/文件下载 ==")
img_key = m1["parts"][1]["id"]; file_key = m1["parts"][2]["id"]
s, data = get("http://%s:%d/api/img?id=%s" % (IP, PORT, urllib.parse.quote(img_key)), raw=True)
print("  /api/img ->", s, "匹配:", data == IMG)
s, data = get("http://%s:%d/api/file?id=%s" % (IP, PORT, urllib.parse.quote(file_key)), raw=True)
print("  /api/file ->", s, "匹配:", data == FILE, "| size字段:", m1["parts"][2].get("size"))
assert data == FILE and m1["parts"][2].get("size") == len(FILE)

print("== 本地保留（模拟重启） ==")
logf, fdir = bg._chat_paths()
print("  数据目录:", os.path.dirname(logf))
print("  记录文件存在:", os.path.isfile(logf), "| 附件数:", len(os.listdir(fdir)) if os.path.isdir(fdir) else 0)
# 模拟重启：清空内存态再触发懒加载
with bg._CHAT_LOCK:
    bg._CHAT_MSGS[:] = []; bg._CHAT_FILES.clear(); bg._CHAT_SEQ[0] = 0
bg._CHAT_LOADED[0] = False
s, d2 = get_json("http://%s:%d/api/messages?since=0" % (IP, PORT))
print("  重启后消息数:", len(d2["messages"]), "（应为2）")
assert len(d2["messages"]) == 2 and d2["messages"][0]["parts"][0]["v"] == "发你一个文件和一张图"
# 重启后附件仍可取回
fk = d2["messages"][0]["parts"][2]["id"]
s, data = get("http://%s:%d/api/file?id=%s" % (IP, PORT, urllib.parse.quote(fk)), raw=True)
print("  重启后文件取回:", s, "匹配:", data == FILE)
assert data == FILE
# 序号继续增长
r3 = post_parts("pc", [{"k": "text", "v": "重启后的新消息"}])
s, d3 = get_json("http://%s:%d/api/messages?since=0" % (IP, PORT))
print("  新消息id:", r3.get("id"), "（应继续增长，共%d条）" % len(d3["messages"]))
assert r3.get("id", 0) > 2

print("== 清空记录 ==")
print("  POST /api/clear:", post("http://%s:%d/api/clear" % (IP, PORT)))
s, d4 = get_json("http://%s:%d/api/messages?since=0" % (IP, PORT))
print("  清空后消息数:", len(d4["messages"]))
assert len(d4["messages"]) == 0
bg._CHAT_LOADED[0] = False
with bg._CHAT_LOCK:
    bg._CHAT_MSGS[:] = []; bg._CHAT_FILES.clear()
s, d5 = get_json("http://%s:%d/api/messages?since=0" % (IP, PORT))
print("  再次重启仍为空:", len(d5["messages"]) == 0)

bg.SHARE.stop(); time.sleep(0.2)
print("\n✅ 富文本+本地保留全部测试通过")
print("（测试数据目录 _chatdata 可手动删除）")
