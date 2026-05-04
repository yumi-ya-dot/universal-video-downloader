# Universal Video Downloader

一个围绕“复制链接或分享文案，即可完成视频解析、下载、字幕提取与内容总结”的工具型网站原型。

当前项目已经打通前后端核心链路，具备从用户输入、视频解析、格式选择、服务端下载落盘，到浏览器触发最终下载的完整闭环。同时，项目还扩展了字幕提取、流式视频总结与视频问答能力，可作为后续持续产品化和工程化迭代的基础版本。

## 项目简介

本项目的目标是降低视频下载的使用门槛：

- 用户既可以输入标准视频链接
- 也可以直接粘贴带有说明、话题、提示语的整段分享文案
- 后端会自动从文本中提取第一个有效 URL
- 再通过统一封装的下载能力完成解析与下载

当前已经完成：

- 视频链接 / 分享文案输入
- 自动提取 URL
- 视频基础信息解析
- 可下载格式展示
- 服务端下载与文件落盘
- 文件访问链接返回
- 浏览器触发最终下载
- 字幕提取
- 视频内容流式总结
- 基于字幕内容的视频问答

## 功能特性

### 1. 视频解析与下载

- 支持输入标准视频链接
- 支持直接粘贴整段分享文案
- 自动抽取首个有效 URL
- 展示视频基础信息：
  - 标题
  - 封面
  - 时长
  - 平台 / 提取器信息
  - 可选格式列表
- 支持选择格式后发起下载
- 后端将文件下载到本地目录，并返回可访问下载链接

### 2. 抖音场景兼容

针对抖音场景，项目已经做过可用性修复：

- 优先使用 `yt-dlp` 原生解析
- 当遇到 fresh cookies 相关问题时，自动切换到分享页兜底解析策略
- 可直接处理抖音分享文案与短链场景

### 3. 字幕与内容理解能力

除下载能力外，当前项目还具备：

- 字幕提取
- 基于字幕文本的视频总结
- 面向视频内容的问答能力
- SSE 流式返回总结 / 问答结果

## 技术栈

### 前端

- Vue 3
- Vite
- TypeScript
- Tailwind CSS Vite 插件
- Mermaid
- svg-pan-zoom

### 后端

- FastAPI
- Uvicorn
- yt-dlp
- pydantic-settings
- requests
- httpx

## 项目结构

```text
universal-video-downloader/
├─ backend/
│  ├─ app/
│  │  ├─ config.py
│  │  ├─ main.py
│  │  ├─ schemas.py
│  │  ├─ sse_utils.py
│  │  ├─ subtitle_extractor.py
│  │  ├─ summary_schemas.py
│  │  ├─ video_summarizer.py
│  │  └─ ytdlp_service.py
│  ├─ main.py
│  ├─ requirements.txt
│  └─ .env
├─ frontend/
│  ├─ public/
│  ├─ src/
│  │  ├─ assets/
│  │  ├─ components/
│  │  ├─ lib/
│  │  ├─ App.vue
│  │  ├─ main.ts
│  │  └─ style.css
│  ├─ package.json
│  └─ vite.config.ts
└─ .gitignore
```

## 已实现接口

后端当前已提供以下接口：

### 基础与下载能力

- `GET /api/health`：健康检查
- `POST /api/parse`：解析视频基础信息与可选格式
- `POST /api/direct-link`：获取直链下载策略结果
- `POST /api/download`：服务端下载视频并返回文件地址
- `GET /api/files/{file_name}`：访问已下载文件
- `GET /api/thumbnail`：封面代理
- `GET /api/download-proxy`：下载代理

### 字幕与总结能力

- `POST /api/subtitles`：提取字幕
- `POST /api/summarize`：流式生成视频总结
- `POST /api/chat`：基于视频字幕内容进行问答

## 运行环境

建议环境：

- Node.js 18+
- npm 9+
- Python 3.10+
- 已安装并可正常使用 `yt-dlp`

## 本地开发启动

### 1. 克隆项目

```bash
git clone https://github.com/yumi-ya-dot/universal-video-downloader.git
cd universal-video-downloader
```

### 2. 启动后端

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r backend/requirements.txt
python backend/main.py
```

默认后端地址：

```text
http://127.0.0.1:8000
```

### 3. 配置后端环境变量

后端会从 `backend/.env` 读取配置。当前代码中涉及的关键配置包括：

- `DEEPSEEK_API_KEY`
- `DEEPSEEK_BASE_URL`
- `DEEPSEEK_MODEL`
- `DEEPSEEK_TIMEOUT`

如果你只想验证基础下载链路，可先不配置大模型能力；如果要使用视频总结 / 问答功能，需要正确配置对应的大模型参数。

### 4. 启动前端

```bash
cd frontend
npm install
npm run dev
```

默认前端地址：

```text
http://127.0.0.1:5173
```

前端开发服务器已通过 `vite.config.ts` 将 `/api` 代理到：

```text
http://127.0.0.1:8000
```

## 构建生产版本

### 前端构建

```bash
cd frontend
npm install
npm run build
```

构建产物默认输出到：

```text
frontend/dist/
```

## 使用流程

项目当前的核心使用流程如下：

1. 用户输入视频链接，或粘贴整段分享文案
2. 后端自动提取第一个有效 URL
3. 调用 `yt-dlp` 解析视频元信息与可选格式
4. 前端展示视频摘要信息与格式列表
5. 用户选择目标格式并发起下载
6. 后端执行下载并将文件保存到本地下载目录
7. 接口返回文件访问地址
8. 浏览器触发最终下载

如果使用字幕 / 总结能力，则在解析视频后进一步：

1. 提取字幕内容
2. 缓存视频与字幕上下文
3. 基于字幕生成流式总结
4. 或围绕视频内容进行追问

## 当前阶段成果判断

从当前版本来看，本项目已经具备以下价值：

### 原型价值

已经不是概念演示，而是具备真实输入、真实解析、真实下载能力的可运行原型。

### 产品价值

已经具备工具站 MVP 的基本形态，后续可继续向以下方向扩展：

- 多平台视频下载工具站
- 会员制效率产品
- 内容处理增强型产品

### 工程价值

- 前后端边界清晰
- 下载能力已做服务层封装
- 具备继续扩展日志、限流、权限、任务队列与部署能力的基础

## 已验证内容

根据当前项目沉淀，已完成并验证：

- 普通视频链接解析
- 抖音短链接解析
- 含 URL 的整段分享文案解析
- 服务端下载
- 文件落盘
- 文件访问 URL 返回
- 浏览器侧下载触发链路
- 前后端联调
- 健康检查返回 `yt-dlp` 版本信息

## 注意事项

### 1. 文档目录默认未提交

当前仓库 `.gitignore` 默认忽略了 `doc/` 目录，因此项目总结、方案设计等内部文档不会随代码一起提交。

### 2. 环境变量默认未提交

`backend/.env` 已被忽略，请自行在本地补充实际配置，不要将敏感信息提交到公开仓库。

### 3. 下载文件目录默认未提交

下载后的媒体文件位于后端下载目录中，默认也不应纳入版本控制。

## 后续迭代建议

### 产品方向

- 下载进度展示
- 下载历史记录
- 多任务队列
- 更多平台分享文案兼容
- 文件自动清理策略

### 商业化方向

- VIP 权益体系
- 高速下载通道
- 批量下载
- 字幕提取 / 翻译 / 总结增强
- 长视频与高成功率增强策略

### 工程化方向

- 更完整的日志体系
- 异常监控与告警
- 限流与风控
- 生产环境配置管理
- 自动化测试
- 自动化部署文档

## 版本结论

当前阶段可以明确认为：

**视频下载核心功能已经完成。**

它目前是一个：

- 已打通核心链路的下载工具站原型
- 可继续产品化与工程化迭代的基础版本
- 适合继续沉淀商业化能力的 MVP 起点

## 仓库地址

GitHub：

- [https://github.com/yumi-ya-dot/universal-video-downloader](https://github.com/yumi-ya-dot/universal-video-downloader)

---

如果你觉得这个项目对你有帮助，欢迎 Star 与继续迭代。