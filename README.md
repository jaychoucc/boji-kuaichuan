<div align="center">

<img src="logo_preview.png" width="120" alt="波吉快传">

# 波吉快传 · Boji Kuaichuan

**手机 ⇄ 电脑，同一个 WiFi，扫码即传。**
不用装 App，不用数据线，不用登录账号。

[![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
![Platform](https://img.shields.io/badge/Platform-Windows-0078D4)
[![Deps](https://img.shields.io/badge/Dependencies-qrcode%20%7C%20Pillow-yellow)](requirements.txt)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>

---

## 这是什么

一个跑在你自己电脑上的**单文件局域网传输工具**。启动后会在本地开一个图形化控制台，手机扫一下二维码连上，就能和电脑互传文件、文字、图片。

灵感与核心服务逻辑来自开源项目 [axiom0x0/bifrost](https://github.com/axiom0x0/bifrost)，本项目用 Python 重写并补齐了**图形化控制台**、**双向富文本传输助手**、**聊天记录本地保留**等能力，同时做到**零 GUI 框架依赖**、可打包成单个 `.exe` 双击即用。

典型场景：

- 手机拍的照片、截的图，想快速弄到电脑上（或反向）
- 电脑上的文件要发到手机，但没装微信/不想经过云端
- 想在手机和电脑之间临时复制一段文字、一张截图
- 家里/办公室里传大文件，走局域网比走网盘快得多

> **数据不出局域网。** 所有传输都在你的路由器和两台设备之间完成，不经过任何第三方服务器。

---

## 功能特性

### 四种工作模式

| 模式 | 说明 |
| :--- | :--- |
| 📤 **发送文件（双向）** | 共享**单个文件**，手机扫码下载；手机也能顺手回传文件给你 |
| 📥 **仅接收文件** | 手机扫码后直接把照片/文件上传到电脑指定目录 |
| 📁 **浏览共享文件夹** | 把**整个文件夹**共享出去，手机端可浏览目录、下载文件，也能回传；含「共享当前目录」一键快捷 |
| 💬 **双向传输助手** | 类似微信「文件传输助手」，电脑与手机互发**文字 / 图片 / 任意文件** |

### 双向传输助手（富文本）

- **Ctrl+V 直接粘贴截图** —— 复制的图片、截屏直接进输入框，显示为缩略图
- **拖拽图片**进输入框即插入
- **文字与图片混排**成一条消息按原顺序发出
- **电脑端可粘贴任意文件**（不只是图片），以文件卡片形式发送，对方点一下即可下载
- **聊天记录本地保留** —— 关闭程序再打开，历史消息和附件都还在
- **大图自动压缩** —— 超过 1600px 或 3MB 的图片发送前自动等比缩小，传输更快
- 气泡式界面，自己绿色靠右、对方灰色靠左，带时间戳

### 其它细节

- **大二维码**展示 + 一键复制链接 + 实时传输日志
- **中文文件名**完整支持，重名文件自动改名（`_1` / `_2`）
- **文件夹选择器显示中文名**（下载 / 文档 / 桌面 / 图片）并提供快捷跳转
- **默认接收目录**为 `文档\下载`，自动创建
- 手机端**无需安装任何 App**，用系统浏览器扫码即可

---

## 快速开始

### 方式一：直接用打包好的 exe（推荐）

从 [**Releases**](https://github.com/jaychoucc/boji-kuaichuan/releases) 页面下载 `波吉快传.exe`，双击运行即可，**不需要安装 Python**。

### 方式二：从源码运行

需要 Python 3.8 及以上。

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动
python bifrost_gui.py
```

Windows 用户也可以直接**双击 `run.bat`**（会自动检查并安装依赖）。

启动后会自动打开浏览器控制台：`http://127.0.0.1:8889/`

---

## 使用流程

1. **双击启动** → 浏览器自动打开控制台（控制台小窗会同时出现，关掉它即退出程序）
2. 在左侧**选择模式** → 挑文件 / 选文件夹 / 设端口
3. 点击 **「▶ 启动共享」** → 右侧立刻出现**二维码**和可复制的链接
4. **手机连上同一个 WiFi**，用相机或浏览器扫码
5. 开始传输。手机端页面上的操作一目了然，无需教程

> 手机和电脑必须处于**同一局域网**（同一路由器/同一 WiFi）。如果扫码打不开，通常是电脑防火墙拦了入站连接，放行 Python 或对应端口即可。

---

## 端口与配置

| 端口 | 用途 | 监听范围 |
| :--- | :--- | :--- |
| `8889` | 图形化控制台 | 仅本机 `127.0.0.1` |
| `8888` | 共享服务（手机访问的页面） | 局域网 |

可用环境变量覆盖：

| 环境变量 | 默认值 | 说明 |
| :--- | :--- | :--- |
| `BIFROST_CONTROL_PORT` | `8889` | 控制台端口。想同时开多个实例时改它 |
| `BIFROST_DATA_DIR` | `%APPDATA%` | 聊天记录与附件的存放根目录（主要用于测试隔离） |

```bash
# 示例：用 8890 作为控制台端口再开一个实例
set BIFROST_CONTROL_PORT=8890
波吉快传.exe
```

---

## 项目结构

```
.
├── bifrost_gui.py        # 主程序：HTTP 服务 + 图形化控制台 + 聊天（单文件）
├── logo_data.py          # 应用图标（base64，由 make_icon.py 生成，勿手改）
├── logo.ico              # exe / 任务栏图标
├── logo_preview.png      # 图标预览
├── 雪纳瑞源图.png         # 图标源图
├── make_icon.py          # 图标处理脚本：裁水印 → 圆形徽章 → ico + base64
├── test_server.py        # 文件传输功能自测
├── test_chat.py          # 双向传输助手自测（含持久化）
├── run.bat               # Windows 双击启动器
├── requirements.txt
├── LICENSE
└── README.md
```

主程序约 **1600 行**，刻意保持单文件，方便直接拷走、改、打包。

---

## HTTP 接口

程序内部起了两个 HTTP 服务。以下接口便于二次开发或自动化调用。

### 控制台服务（`127.0.0.1:8889`）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/` | 控制台页面 |
| `GET` | `/api/state` | 当前运行状态、模式、地址、日志 |
| `GET` | `/api/cwd` | 当前工作目录 |
| `GET` | `/api/browse?path=` | 浏览目录（返回磁盘列表、条目、中文名、快捷跳转） |
| `GET` | `/api/qr?text=` | 生成二维码 PNG |
| `POST` | `/api/start` | 启动共享。body: `{mode, port, file/dir, output}` |
| `POST` | `/api/stop` | 停止共享 |

### 共享服务（`0.0.0.0:8888`，手机访问）

| 方法 | 路径 | 说明 |
| :--- | :--- | :--- |
| `GET` | `/` | 按当前模式渲染手机端页面（`?side=phone\|pc` 区分聊天端） |
| `GET` | `/download/<文件名>` | 下载共享文件 |
| `GET` | `/api/messages?since=` | 增量拉取聊天消息 |
| `GET` | `/api/img?id=` | 取聊天图片 |
| `GET` | `/api/file?id=` | 取聊天附件 |
| `POST` | `/api/send?side=` | 发送富文本消息，body: `{parts:[{k:'text'\|'img'\|'file', ...}]}` |
| `POST` | `/api/clear` | 清空聊天记录 |
| `POST` | `/upload` | 上传文件（`multipart/form-data`） |

---

## 数据存放

聊天记录与附件默认保存在：

```
%APPDATA%\波吉快传\
├── chat_log.json     # 消息记录
└── chat_files\       # 图片与文件附件
```

最多保留最近 **200 条**消息。想彻底清空，用聊天页的「清空」按钮，或直接删掉这个目录。

---

## 打包成 exe

```bash
pip install pyinstaller
pyinstaller --onefile --console --name "波吉快传" --icon logo.ico --clean --noconfirm bifrost_gui.py
```

产物在 `dist/波吉快传.exe`，约 27MB，零外部依赖。

两个打包相关的注意点（踩过的坑）：

- `logo_data.py` 通过 `import` 引入，PyInstaller 会自动一并打进包里，**不需要**额外 `--add-data`。
- 程序启动时对 `stdout/stderr` 做了 `reconfigure(errors="replace")`。中文 Windows 控制台默认 GBK，启动横幅里的 emoji 会触发 `UnicodeEncodeError` 直接闪退 —— `.py` 在 UTF-8 终端下不会复现，但打成 exe 后必现。**改动启动日志时请保留这段兼容处理。**

---

## 更换图标

1. 把新的源图（文件名需包含 `雪纳瑞`，或改 `make_icon.py` 里的 glob）放到项目根目录
2. 运行：

```bash
python make_icon.py
```

会重新生成 `logo.ico`、`logo_data.py`、`logo_preview.png`。重新打包 exe 即可看到新图标。

---

## 测试

```bash
python test_server.py   # 三种文件传输模式：下载 / 上传 / 浏览 / 重名改名 / 中文文件名
python test_chat.py     # 富文本混排、多图、文件下载、持久化往返、清空、路径穿越防护
```

两个脚本都会临时起服务、跑断言、结束时清理测试数据，不依赖外部网络。

---

## 已知限制与安全提示

> ⚠️ **本工具没有身份认证，也没有传输加密（明文 HTTP）。**
> 任何能访问到该地址的同一局域网设备都可以下载共享文件、上传文件、读取聊天内容。
> **请只在可信网络（如家里、自己的办公室）使用，不要在公共 WiFi 下共享敏感文件。**
> 传输结束后请及时点击「⏹ 停止共享」。

其它限制：

- **手机端粘贴图片受浏览器限制**：移动端浏览器普遍不开放剪贴板图片读取，请用「选图 / 拍照」按钮插入图片；电脑端 Ctrl+V 粘贴体验最完整。
- 单条消息最多 **10 张图**，单图上限 **30MB**，单个附件上限 **100MB**。
- **优先支持 Windows**：解析真实「下载」目录（注册表）、枚举盘符（`GetLogicalDrives`）等用了 Windows API；其它平台会降级到常规路径。
- 聊天记录只保留最近 200 条，超出后最早的会被淘汰。

---

## 技术选型说明

几个关键决策及理由：

- **为什么用本地 Web 控制台而不是 tkinter/Qt？**
  目标机器的 Python 分发不一定带 tkinter（Windows Store 版、部分精简版就没有），而 Qt 体积大、打包后 exe 会膨胀到上百 MB。用浏览器当 UI，**零 GUI 依赖、跨平台、界面也更现代**，打包产物依然只有 27MB。

- **为什么用标准库 `http.server` 而不是 Flask/FastAPI？**
  依赖越少，exe 越小、越不容易被杀软误报。本项目只需要几个路由 + 静态页面，`ThreadingHTTPServer` 完全够用。

- **为什么列盘符不用 `os.path.isdir` 逐个探测？**
  遇到空光驱或已断开的网络映射盘会**阻塞数秒甚至更久**。改用 Windows `GetLogicalDrives` 只查询「存在哪些盘」、不访问介质，毫秒级返回。

- **为什么单文件？**
  方便你直接把 `.py` 拷到任何一台装了 Python 的机器上跑，也方便改一行就重新打包。

---

## 致谢

- 服务端逻辑与移动端页面设计参考 [axiom0x0/bifrost](https://github.com/axiom0x0/bifrost)（Go 实现）。本项目为独立的 Python 重写，并扩展了图形化控制台与双向传输助手。
- 图标为 AI 生成后经 `make_icon.py` 程序化处理成圆形徽章。

---

## License

[MIT](LICENSE)
