# LocalPrint 局域网自助打印系统与微信小程序

基于 Flask + CUPS + Watchdog 构建的局域网全自动文档打印系统，配备专用**微信小程序客户端**，支持手机微信聊天记录秒选、图片、压缩包云解压与单双面/份数多参数批量打印。

---

## 🌟 核心特性

- 📱 **微信小程序客户端 (`/miniprogram`)**
  - **秒选聊天记录**：支持多选微信文档（Word/Excel/PPT/PDF/TXT等）、微信图片、手机相册。
  - **压缩包云端解压**：在线解压 ZIP / TAR / GZ 等压缩包，清单勾选，彻底解决 Windows GBK 与 macOS UTF-8 中文乱码。
  - **批量与独立设置**：多文档批量上传，支持每个文档独立设置单双面（长边/短边翻转）、份数、页码范围。
  - **打印历史管理**：按天自动分组折叠显示历史记录，今日任务默认展开，实时展示页数与状态。
  - **固化与动态配置**：内置默认打印机服务器地址，一键弹窗修改并持久化保存。

- 🖨️ **后端打印服务 (`app.py`, `services/`)**
  - **CUPS 打印机集成**：自动识别 CUPS 队列，真实文档名与页数映射解析。
  - **格式自动转换**：Office 文档通过 LibreOffice 无损转换为 PDF。
  - **多通道监听**：支持文件夹文件拖入监听自动打印与 HTTP RESTful API 调用。
  - **跨域与异步处理**：全面支持 Flask-CORS 跨域，解压与打印任务全异步多线程处理。

---

## 📁 目录结构

```text
├── app.py                     # Flask Web 与 API 服务入口
├── config.py                  # 基础配置项（目录、端口、支持扩展名）
├── database.py                # SQLite 历史数据库管理
├── services/                  # 后台服务模块
│   ├── conversion.py          # 文档转PDF与编码清洗引擎 (EncodingHandler)
│   ├── file_monitor.py        # 文件监控与自动化解压打印处理
│   └── print_manager.py       # CUPS 打印队列管理与页数解析
├── miniprogram/               # 微信小程序源码
│   ├── pages/index/           # 主界面（文件上传、压缩包预览、打印历史）
│   ├── app.js / app.json      # 小程序全局配置
│   └── project.config.json    # 微信开发者工具工程配置
├── templates/                 # 网页端交互模板
└── requirements.txt           # Python 依赖包清单
```

---

## 🚀 快速启动指南

### 1. 服务端部署（Linux / CUPS 环境）

```bash
# 1. 安装系统依赖（CUPS 与 LibreOffice）
sudo apt-get update
sudo apt-get install -y cups cups-client libreoffice pypdf python3-pip

# 2. 安装 Python 依赖
pip install flask flask-cors watchdog pypdf

# 3. 启动服务
python3 app.py
```

服务默认运行在 `http://0.0.0.0:5005`。

### 2. 小程序导入与调试

1. 打开**微信开发者工具**。
2. 点击【导入项目】，选择项目中的 `miniprogram` 目录。
3. AppID 可先选择**测试号**或输入您注册的小程序 AppID。
4. 在开发者工具【详情】->【本地设置】中，勾选 **“不校验合法域名、web-view (业务域名)、TLS 版本以及 HTTPS 证书”**。
5. 手机与打印服务器连接在**同一个 Wi-Fi / 局域网**，即可点击【预览】在真机扫码使用！

---

## 📄 开源许可证

MIT License
