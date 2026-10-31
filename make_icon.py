# -*- coding: utf-8 -*-
"""把生成的雪纳瑞图处理成应用图标：裁水印→方形裁剪→圆形徽章→logo.ico + 内嵌 base64(logo_data.py)。"""
import os, glob, base64, io
from PIL import Image, ImageDraw

base = os.path.dirname(os.path.abspath(__file__))
srcs = sorted(glob.glob(os.path.join(base, "*雪纳瑞*.png")), key=os.path.getmtime)
if not srcs:
    raise SystemExit("找不到雪纳瑞源图")
SRC = srcs[-1]
print("源图:", os.path.basename(SRC))

img = Image.open(SRC).convert("RGB")
W, H = img.size

# 1) 抹掉右下角“AI生成”水印（涂成背景白），再定位狗的黑线区域
g = img.convert("L")
ImageDraw.Draw(g).rectangle([int(0.84 * W), int(0.88 * H), W, H], fill=255)
bw = g.point(lambda v: 255 if v < 120 else 0)  # 黑线 -> 白(非零)，背景 -> 黑(0)
bbox = bw.getbbox()
if not bbox:
    raise SystemExit("未检测到狗的黑线区域")
x0, y0, x1, y1 = bbox

# 2) 以狗为中心做方形裁剪（四周留白）
cw, ch = x1 - x0, y1 - y0
side = int(max(cw, ch) * 1.22)
cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
half = side // 2
left, top = max(0, cx - half), max(0, cy - half)
dog = img.crop((left, top, min(W, left + side), min(H, top + side)))


def badge(size):
    dg = dog.resize((size, size), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size - 1, size - 1], fill=255)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(dg, (0, 0), mask)  # 白底黑狗，圆形
    ring = ImageDraw.Draw(out)
    w = max(2, size // 72)
    ring.ellipse([w, w, size - 1 - w, size - 1 - w], outline=(13, 17, 23, 255), width=w)
    return out


# 3) exe 图标（多尺寸 ico）
badge(256).save(os.path.join(base, "logo.ico"),
                sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

# 4) 网页内嵌/favicon 用 96px，base64 写入 logo_data.py（用内存缓冲，不落临时文件）
buf = io.BytesIO()
badge(96).save(buf, format="PNG")
uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
with open(os.path.join(base, "logo_data.py"), "w", encoding="utf-8") as f:
    f.write("# -*- coding: utf-8 -*-\n# 由 make_icon.py 自动生成，请勿手改\nLOGO_URI = \"%s\"\n" % uri)

# 5) 预览图
badge(256).save(os.path.join(base, "logo_preview.png"))
print("OK  bbox=", bbox, " uri长度=", len(uri))
print("生成: logo.ico, logo_data.py, logo_preview.png")
