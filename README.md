# LocalPrint 局域网自助打印系统与微信小程序

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8+-green.svg)](https://www.python.org/)
[![CUPS](https://img.shields.io/badge/Print-CUPS-orange.svg)](https://www.cups.org/)
[![WeChat](https://img.shields.io/badge/Client-WeChat_MiniProgram-brightgreen.svg)](https://mp.weixin.qq.com/)

**LocalPrint** 是一套专为家庭、办公室、实验室或校园打造的**局域网私有化自助打印系统**。包含：
- 📱 **微信小程序客户端**：手机无需安装任何第三方 App，直接从微信聊天记录秒选各类文档，在局域网内直连打印机；
- 🖥️ **Web 管理控制台**：支持多文件批量上传、按天归档、队列实时监控与一键取消；
- ⚙️ **自动化打印与转换核心**：多格式文档自动无损转 PDF、压缩包智能解码防乱码解包、目录监听“丢入即打”无感出纸。

---

## 📑 目录
- [系统架构与工作流程](#-系统架构与工作流程)
- [核心自动化逻辑详解](#-核心自动化逻辑详解)
  - [1. 目录监听与出纸规则（file 与 autofile）](#1-目录监听与出纸规则)
  - [2. 压缩包智能云端解压（防乱码引擎）](#2-压缩包智能云端解压防乱码引擎)
  - [3. 文档自动转换引擎（LibreOffice Headless）](#3-文档自动转换引擎)
- [服务器硬件与系统选型推荐](#-服务器硬件与系统选型推荐)
- [CUPS 打印系统安装与实机验证指南](#-cups-打印系统安装与实机验证指南)
  - [第一步：安装 CUPS 及打印机驱动](#第一步安装-cups-及打印机驱动)
  - [第二步：配置 CUPS 允许远程 Web 管理](#第二步配置-cups-允许远程-web-管理)
  - [第三步：在 Web 界面添加打印机并设为默认](#第三步在-web-界面添加打印机并设为默认)
  - [第四步：打印测试页与命令行验证（关键步骤）](#第四步打印测试页与命令行验证关键步骤)
- [服务部署与运行](#-服务部署与运行)
- [微信小程序端配置](#-微信小程序端配置)

---

## 🌟 系统架构与工作流程

```mermaid
flowchart TD
    subgraph 客户端 [多端客户端]
        MP["📱 微信小程序客户端 (微信聊天文档/图片/压缩包)"]
        WEB["💻 Web 控制台 (PC 浏览器批量操作)"]
        SAMBA["📁 局域网共享目录 (Samba/NFS/本地文件夹)"]
    end

    subgraph 后端服务 [LocalPrint 后端服务 (Flask + Watchdog)]
        API["RESTful API 接口 (/api/upload, /api/history)"]
        WATCH["Watchdog 目录监听引擎"]
        UNZIP["📦 压缩包智能解压与编码清洗 (GBK/UTF-8)"]
        CONVERT["🔄 LibreOffice Headless 无损转 PDF 引擎"]
        DB[(SQLite 任务与历史数据库)]
    end

    subgraph 打印底层 [Linux CUPS 打印系统]
        SPOOL["CUPS Spooler (队列管理 / lp / cancel)"]
        PRINTER["🖨️ 物理网络/USB 打印机 (自动双面/单面出纸)"]
    end

    MP -->|"HTTP POST (局域网直连)"| API
    WEB -->|"HTTP POST / WebSocket"| API
    SAMBA -->|"放入 file/ 仅转换"| WATCH
    SAMBA -->|"放入 file/autofile/ 自动打"| WATCH

    API --> UNZIP
    API --> CONVERT
    WATCH --> UNZIP
    WATCH --> CONVERT

    UNZIP --> CONVERT
    CONVERT --> DB
    CONVERT -->|"提交打印"| SPOOL
    SPOOL --> PRINTER
```

---

## ⚙️ 核心自动化逻辑详解

### 1. 目录监听与出纸规则

系统启动后，后台守护进程会自动实时监控 `file/` 及其子目录的变化：

| 目录路径 | 触发时机 | 自动化处理流程 | 适用场景 |
| :--- | :--- | :--- | :--- |
| **`file/`** | 拖入/拷贝新增文档 | 1. 自动解包压缩包并清洗文件<br>2. 自动调用无头引擎转为 PDF 存入 `converted_pdfs/`<br>3. **仅转换，不出纸**，并在 Web 与小程序列表呈现 | **手动挑选打印**：需要人工预览确认、挑页打印或稍后批量打印的文档 |
| **`file/autofile/`** | 拖入/拷贝新增文档 | 1. 自动完成解压与格式转换<br>2. **立即直接调用物理打印机出纸**（默认长边双面打印）<br>3. 打印完成后自动记录数据库并归档 | **无感极速打印**：电脑端配置局域网 Samba 共享后，把文件拖进该目录即可听见打印机出纸，完全免开网页 |

### 2. 压缩包智能云端解压（防乱码引擎）
- **格式支持**：全面支持 `.zip`、`.tar`、`.gz`、`.bz2`、`.7z`、`.rar`；
- **智能编码清洗**：彻底解决跨平台（Windows GBK 与 macOS/Linux UTF-8）解压缩中文文件名乱码问题；
- **多层递归提取**：自动剔除 `__MACOSX`、`.DS_Store`、`Thumbs.db` 等系统垃圾缓存文件；
- **小程序可视化挑选**：小程序支持直接上传压缩包，服务端秒级解压后向小程序返回内部文档清单，支持用户逐个勾选打印。

### 3. 文档自动转换引擎
- **支持格式**：
  - 办公文档：`Word (.doc, .docx)`、`Excel (.xls, .xlsx)`、`PPT (.ppt, .pptx)`、`文本 (.txt)`
  - 图像文件：`.jpg`、`.jpeg`、`.png`、`.bmp`、`.tiff`
  - 原生支持：`.pdf`
- **处理方式**：通过无头 LibreOffice 环境在后台完成无损排版转换，严格保留页面原始布局与字体；
- **页数自动解析**：转换完成后由 `pypdf` 高性能引擎瞬间提取总页数，供界面即时预览计算。

---

## 🖥️ 服务器硬件与系统选型推荐

LocalPrint 对硬件配置要求极低，根据使用环境推荐以下三种部署硬件：

### 推荐方案一：低功耗 x86 迷你主机 / 软路由（🌟 强烈推荐）
- **典型硬件**：Intel N100 / N5105 / J4125 / J1900 等工控机、迷你准系统或软路由；
- **内存建议**：2GB ~ 8GB；
- **优势**：
  - x86 架构拥有最全面的打印机驱动生态（HP、Epson、Canon、Brother、Lenovo 等官方 Linux 驱动完备）；
  - LibreOffice 文档转换速度极快（1~2 秒即可转换几十页 Word）；
  - 整机功耗仅 6W~15W，全天 24 小时不关机稳定静音。

### 推荐方案二：ARM 单板开发板（极致节能、高性价比）
- **典型硬件**：树莓派 (Raspberry Pi 3B+ / 4B / 5)、香橙派 (Orange Pi)、玩客云等；
- **优势**：体积仅有卡片大小，功耗仅 3W~5W，配合 Gutenprint 通用开源驱动即可将任意老旧 USB 打印机变身为无线网络打印机。

### 推荐方案三：家用 NAS 或闲置旧电脑
- **典型硬件**：群晖、威联通、Unraid、TrueNAS（通过虚拟机部署），或退役的旧笔记本电脑。

### 操作系统推荐
- **首选**：**Ubuntu Server 22.04 LTS / 24.04 LTS** 或 **Debian 11 / 12**（包管理体系对 CUPS 与 LibreOffice 支持最完善）。

---

## 🖨️ CUPS 打印系统安装与实机验证指南

在启动 LocalPrint 前，必须**先配置好 CUPS 打印底层并打印测试页**，确保服务器与物理打印机之间通信 100% 畅通。

### 第一步：安装 CUPS 及打印机驱动
登录服务器终端，执行以下命令安装 CUPS 服务及常见开源驱动套件：

```bash
sudo apt-get update
sudo apt-get install -y cups cups-client cups-filters printer-driver-gutenprint hplip
```

### 第二步：配置 CUPS 允许远程 Web 管理
CUPS 默认仅允许本机访问，需开启局域网远程管理权限：

```bash
# 1. 将当前用户加入 lpadmin 打印管理员组
sudo usermod -aG lpadmin $USER

# 2. 开启远程访问与局域网打印机共享
sudo cupsctl --remote-admin --remote-any --share-printers

# 3. 重启 CUPS 服务
sudo systemctl restart cups
```

### 第三步：在 Web 界面添加打印机并设为默认
1. 在同一局域网的电脑浏览器中打开：
   ```text
   https://<服务器局域网IP>:631
   ```
   *(例如 `https://192.168.10.66:631`，浏览器提示自签名证书不安全时，点击“高级” -> “继续访问”)*。
2. 点击顶部导航栏的 **Administration** -> 点击 **Add Printer**（会弹出认证窗口，输入服务器的 Linux 用户名与密码）。
3. **选择打印机连接方式**：
   - 如果是 USB 打印机：直接选择列表中识别出的 Local USB Printer；
   - 如果是网络打印机：选择 `AppSocket/HP JetDirect` 或 `Internet Printing Protocol (ipp/http)`，输入网络打印机 IP（例如 `socket://192.168.10.200:9100` 或 `ipp://192.168.10.200/ipp/print`）。
4. **选择打印机驱动**：选择对应的品牌（如 Epson、HP、Canon）和型号；若无完全一致型号，可优先选择带 `Gutenprint` 的推荐驱动或 `Generic PCL/PostScript` 驱动。
5. **设为系统默认打印机（极关键）**：
   - 添加成功后进入该打印机页面；
   - 点击 **Administration** 下拉菜单，选择 **Set As Server Default**（设为系统默认打印机）。

### 第四步：打印测试页与命令行验证（关键步骤）

在接入 LocalPrint 业务系统前，请按以下两种方式验证物理出纸是否成功：

#### 验证方法 A：Web 控制台打印测试页
在 CUPS 打印机详情页：
- 点击 **Maintenance** 下拉菜单；
- 点击 **Print Test Page**；
- **观察物理打印机是否成功进纸，并打印出一张带有彩色图形的 CUPS 官方测试页**。

#### 验证方法 B：Linux 终端命令行验证
打开服务器终端，运行以下命令测试底层通信：

```bash
# 1. 查看默认打印机及就绪状态
lpstat -p -d
# 正常输出示例：
# system default destination: EPSON_L6260_Series
# printer EPSON_L6260_Series is idle.  enabled since ...

# 2. 打印一行纯文本测试
echo "Hello LocalPrint! CUPS is working properly." | lp

# 3. 打印一份本地 PDF 并测试双面长边翻转
lp -o sides=two-sided-long-edge /path/to/test.pdf

# 4. 查看当前作业队列
lpstat -o
```

> [!TIP]
> 只要上述步骤能正常打印出纸，即证明 Linux 底层打印环境完全正常，可以正式启动 LocalPrint 服务！

---

## 🚀 服务部署与运行

### 1. 安装系统与 Python 依赖
```bash
sudo apt-get update
sudo apt-get install -y libreoffice pypdf python3-pip

cd /root/docker/lp
pip install -r requirements.txt
```

### 2. 启动服务
```bash
python3 app.py
```
服务将在 `http://0.0.0.0:5005` 启动。此时在浏览器访问 `http://<服务器IP>:5005` 即可打开 Web 控制台。

### 3. 配置 Systemd 开机自启（推荐长期运行）
项目内已包含标准 service 文件 [mon_print.service](file:///c:/project/lp/mon_print.service)：

```bash
sudo cp mon_print.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable mon_print
sudo systemctl start mon_print

# 检查服务运行状态
sudo systemctl status mon_print
```

---

## 📱 微信小程序端配置

1. 打开 **微信开发者工具**，选择【导入项目】；
2. 项目目录选择仓库中的 `miniprogram/` 文件夹；
3. AppID 填入您在微信公众平台申请的小程序 AppID（或选择测试号）；
4. 进入【详情】->【本地设置】，勾选 **“不校验合法域名、web-view (业务域名)、TLS 版本以及 HTTPS 证书”**；
5. 手机与服务器连接到**同一个 Wi-Fi / 局域网**；
6. 在小程序界面右上角点击【修改】，填入服务器的内网 IP 与端口（例如 `http://192.168.10.66:5005`）；
7. 点击微信开发者工具的【预览】或【真机调试】，即可在手机上畅快体验局域网自主打印！

---

## 📄 开源许可证

本项目基于 [MIT 许可证](LICENSE) 开源。欢迎提交 Issue 与 Pull Request 共同改进！
