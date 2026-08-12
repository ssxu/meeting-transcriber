# 会议转录平台（Meeting Transcriber）

自托管会议录音转录 + 大模型纪要平台。后端 Python/FastAPI，前端 Vue3/Naive UI，
调用 **Qwen3-ASR** 做语音识别，再调用 LLM 生成结构化纪要。

## 功能

### 核心功能
- 📤 录音文件上传（音频/视频均可），落盘持久化
- 🎤 **浏览器实时录音**（MediaRecorder），录完自动上传转录
- 🎙️ 调用 Qwen3-ASR 异步转录（逐字稿 + 分段 + 说话人 + 时长）
- 👥 **说话人分段展示**（ASR 返回 speaker_id，自动渲染）
- 🤖 调用 LLM 自动生成 Markdown 会议纪要
- 🏷️ **关键词自动提取**（LLM 提取 3-8 个标签）
- ✅ **待办事项提取**（从纪要中结构化提取）
- 🔍 **全文搜索**（逐字稿 + 摘要 + 文件名 + 标题）

### 声纹 & 热词 & 会议类型管理（v1.1 新增）
- 🎤 **声纹管理** - 注册说话人声纹，与ASR后端同步，支持添加/删除样本
- 📝 **热词库管理** - 创建热词库，批量导入热词，上传录音时选择热词库提升识别准确率
- 🏷 **会议类型管理** - 自定义不同会议类型的LLM总结提示词，按场景定制纪要风格
- ⚙️ **上传配置** - 上传时可选择热词库和会议类型，灵活适配不同场景

### 产品化功能（参考讯飞听见/阿里听悟/钉钉听见）
- 📊 **仪表盘** - 录音总数、总时长、状态分布、近7天趋势
- ✏️ **录音重命名** - 点击标题可编辑
- 📋 **批量操作** - 批量选择、批量删除、批量导出
- ⏱️ **时间轴逐字稿** - 点击时间戳跳转音频播放
- 👤 **说话人筛选** - 按说话人筛选逐字稿内容
- 📋 **一键复制** - 复制全文/摘要/待办事项
- 🔗 **分享功能** - 生成只读分享链接
- 📄 **导出 Markdown** - 单个/批量导出
- 📁 **本地文件存储** - 录音、转录文本、摘要分别存到子目录，方便映射 NAS
- 🌙 **深色模式**切换
- 🐳 Docker Compose 一键部署

## 架构

```
Vue3(Naive UI)  ──HTTP──▶  FastAPI  ──OpenAI API──▶  Qwen3-ASR (192.168.100.51:7034)
                                      └──OpenAI API──▶  LLM (Ollama/vLLM 等)
```

## 存储结构

```
data/                          # 存储根目录（可映射到 NAS）
├── meeting.db                 # SQLite 数据库
├── recordings/                # 音频文件
├── transcripts/               # 转录文本（.txt）
└── summaries/                 # 摘要文本（.md）
```

## 快速开始

### 1. 配置环境变量

复制 `backend/.env.example` 为 `backend/.env`，修改 ASR/LLM 地址：

```env
# ASR 服务
ASR_BASE_URL=http://192.168.100.51:7034
ASR_TIMEOUT=600

# LLM 服务
LLM_BASE_URL=http://host.docker.internal:11434
LLM_API_KEY=ollama
LLM_MODEL=qwen3:latest
LLM_TIMEOUT=300

# 存储配置
STORAGE_PATH=/data
DATABASE_URL=sqlite+aiosqlite:////data/meeting.db
```

### 2. Docker 部署（NAS 推荐）

```bash
docker compose up -d --build
# 前端 http://<NAS_IP>      后端 API http://<NAS_IP>:8000
```

`docker-compose.yml` 中使用 bind mount：
```yaml
volumes:
  - ./data:/data    # 替换 ./data 为你的 NAS 挂载路径
```

### 3. 本地开发

```bash
# 后端
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev   # http://localhost:5173，已代理 /api -> :8000
```

## ASR 服务接口

本项目使用 Qwen3-ASR Server（OpenAI 兼容接口）：

| 项目 | 说明 |
|------|------|
| 端点 | `POST /v1/audio/transcriptions` |
| 请求参数 | `file`（二进制）、`response_format=verbose_json`、`enable_speaker_diarization=true`、`vocabulary_id`（热词库） |
| 响应字段 | `result`（文本）、`segments`（含 `start_time`/`end_time`/`speaker_id`）、`duration` |
| 健康检查 | `GET /stream/v1/asr/health` |
| 模型列表 | `GET /v1/models` |
| 声纹列表 | `GET /api/v1/voiceprint-speakers` |
| 创建说话人 | `POST /api/v1/voiceprint-speakers`（form-data: display_name, file[]） |
| 添加样本 | `POST /api/v1/voiceprint-speakers/{speaker_id}/samples` |
| 删除说话人 | `DELETE /api/v1/voiceprint-speakers/{speaker_id}` |

> 注意：ASR 服务不接收 `model` 参数，模型通过服务端环境变量配置。

## API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | /api/recordings/upload | 上传录音（支持hotword_library_id, meeting_type_id），自动触发转录+总结 |
| GET  | /api/recordings | 录音列表 |
| GET  | /api/recordings/search?q=xxx | 全文搜索 |
| GET  | /api/recordings/{id} | 详情（含逐字稿+摘要+关键词+待办） |
| GET  | /api/recordings/{id}/audio | 音频流 |
| PATCH| /api/recordings/{id}/title | 重命名 |
| POST | /api/recordings/{id}/summarize | 重新生成摘要 |
| DELETE | /api/recordings/{id} | 删除录音+文件 |
| POST | /api/recordings/batch-delete | 批量删除 |
| GET  | /api/recordings/batch-export?ids=1,2,3 | 批量导出 |
| GET  | /api/recordings/{id}/export | 导出单个 Markdown |
| POST | /api/recordings/{id}/share | 生成分享链接 |
| DELETE | /api/recordings/{id}/share | 撤销分享 |
| GET  | /api/stats | 仪表盘统计 |
| GET  | /api/share/{token} | 只读分享访问 |
| **热词库** | | |
| GET  | /api/hotwords/libraries | 热词库列表 |
| POST | /api/hotwords/libraries | 创建热词库 |
| GET  | /api/hotwords/libraries/{id} | 热词库详情（含热词列表） |
| PATCH| /api/hotwords/libraries/{id} | 更新热词库 |
| DELETE | /api/hotwords/libraries/{id} | 删除热词库 |
| POST | /api/hotwords/libraries/{id}/words | 批量添加热词 |
| POST | /api/hotwords/libraries/{id}/import | 文本批量导入热词 |
| DELETE | /api/hotwords/words/{id} | 删除单个热词 |
| **会议类型** | | |
| GET  | /api/meeting-types | 会议类型列表 |
| POST | /api/meeting-types | 创建会议类型 |
| GET  | /api/meeting-types/{id} | 会议类型详情 |
| PATCH| /api/meeting-types/{id} | 更新会议类型 |
| DELETE | /api/meeting-types/{id} | 删除会议类型 |
| **声纹管理** | | |
| GET  | /api/voiceprints | 声纹说话人列表（本地） |
| GET  | /api/voiceprints/sync | 从ASR后端同步声纹列表 |
| POST | /api/voiceprints | 注册说话人（上传音频样本） |
| POST | /api/voiceprints/{id}/samples | 添加声纹样本 |
| PATCH| /api/voiceprints/{id} | 更新说话人信息 |
| DELETE | /api/voiceprints/{id} | 删除说话人 |
| GET  | /api/health | 健康检查 |

## 目录结构

```
meeting-transcriber/
├── backend/
│   ├── app/
│   │   ├── config.py          # 环境变量配置
│   │   ├── db.py              # 异步 DB 会话
│   │   ├── models.py          # Recording + HotwordLibrary + Hotword + MeetingType + VoiceprintSpeaker
│   │   ├── schemas.py         # Pydantic 响应模型
│   │   ├── main.py            # FastAPI 入口 + CORS + 全局异常 + 路由注册
│   │   ├── services/
│   │   │   ├── asr.py         # ASR 转录 + 声纹管理（创建/添加样本/删除/同步）
│   │   │   └── llm.py         # LLM 摘要（支持自定义提示词）
│   │   ├── routes/
│   │   │   ├── recordings.py  # 上传/列表/详情/搜索/删除/导出/重命名/批量/分享
│   │   │   ├── voiceprints.py # 声纹管理路由
│   │   │   ├── hotwords.py    # 热词库管理路由
│   │   │   ├── meeting_types.py # 会议类型管理路由
│   │   │   ├── stats.py       # 仪表盘统计
│   │   │   └── share.py       # 只读分享
│   │   └── utils/
│   │       └── storage.py     # 文件落盘 + Webhook 通知
│   ├── Dockerfile
│   ├── .env.example
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── api/index.js       # Axios 实例 + 全部API封装
│   │   ├── router/index.js    # 路由（列表/详情/仪表盘/分享/声纹/热词/会议类型）
│   │   ├── App.vue            # 布局 + 深色模式 + 导航栏 + 管理下拉菜单
│   │   ├── components/
│   │   │   └── AudioPlayer.vue # 音频播放器组件
│   │   └── views/
│   │       ├── RecordingList.vue    # 列表 + 搜索 + 录音 + 批量 + 上传配置弹窗
│   │       ├── RecordingDetail.vue  # Tab式详情 + 时间轴 + 说话人筛选
│   │       ├── Dashboard.vue        # 仪表盘统计
│   │       ├── SharedView.vue       # 只读分享页
│   │       ├── VoiceprintManager.vue # 声纹管理
│   │       ├── HotwordManager.vue    # 热词库管理
│   │       └── MeetingTypeManager.vue # 会议类型管理
│   ├── Dockerfile             # 多阶段构建 → nginx
│   ├── nginx.conf             # SPA + API 反代
│   └── package.json
└── docker-compose.yml         # bind mount 到 ./data 目录
```
