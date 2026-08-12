# Meeting Transcriber MCP 搜索技能

## 描述

通过 MCP (Model Context Protocol) 连接 Meeting Transcriber 服务，搜索和检索会议录音的摘要、关键词、待办事项等信息。

## 适用场景

- "帮我搜索关于 XXX 主题的会议"
- "找一下最近讨论 YYY 的录音"
- "上次关于 ZZZ 的会议决定了什么？"
- "列出最近的会议录音"
- "获取录音 ID 为 N 的详细信息"

## 前置条件

- Meeting Transcriber 后端服务已运行
- 已配置 MCP_TOKEN（如有需要）

## 使用方法

### 1. 配置 MCP 连接

在支持 MCP 的客户端中添加以下 MCP Server 配置：

**Streamable HTTP 方式（推荐）：**

```json
{
  "mcpServers": {
    "meeting-transcriber": {
      "url": "http://<服务器地址>:8000/mcp",
      "headers": {
        "Authorization": "Bearer <MCP_TOKEN>"
      }
    }
  }
}
```

> 如果 MCP_TOKEN 为空，则不需要 headers 字段。

### 2. 可用工具

连接成功后，以下工具将可用：

#### search_recordings
搜索会议录音。

**参数：**
- `query` (string, 必填): 搜索关键词或自然语言查询
- `mode` (string, 可选): 搜索模式 `keyword` | `semantic` | `hybrid` | `auto`，默认 `auto`
- `limit` (integer, 可选): 返回数量上限，默认 10，最大 50

**返回：**
```json
{
  "total": 3,
  "mode": "hybrid",
  "items": [
    {
      "id": 42,
      "title": "周会-项目进度同步",
      "summary": "本次会议讨论了...",
      "created_at": "2026-07-20 14:30",
      "duration_sec": 3600,
      "keywords": ["项目进度", "里程碑"],
      "tags": ["周会"],
      "score": 0.89
    }
  ]
}
```

#### get_recording_detail
获取单个录音的详细信息。

**参数：**
- `recording_id` (integer, 必填): 录音 ID
- `include_transcript` (boolean, 可选): 是否包含逐字稿全文，默认 false

**返回：**
```json
{
  "id": 42,
  "title": "周会-项目进度同步",
  "summary": "完整的会议摘要...",
  "notes": "用户备注内容...",
  "created_at": "2026-07-20 14:30",
  "duration_sec": 3600,
  "keywords": ["项目进度", "里程碑"],
  "tags": ["周会"],
  "action_items": ["完成需求文档", "安排下一轮评审"],
  "engine": "qwen_asr",
  "speakers": ["说话人1", "说话人2", "说话人3"]
}
```

#### list_recent_recordings
列出最近的已完成录音。

**参数：**
- `limit` (integer, 可选): 返回数量上限，默认 10，最大 50

**返回：**
```json
{
  "total": 5,
  "items": [
    {
      "id": 42,
      "title": "周会-项目进度同步",
      "summary": "摘要前500字...",
      "created_at": "2026-07-20 14:30",
      "duration_sec": 3600,
      "keywords": ["项目进度"],
      "tags": ["周会"]
    }
  ]
}
```

### 3. 使用示例

**搜索会议：**
```
用户：帮我找一下关于交换机部署的会议
AI：[调用 search_recordings(query="交换机部署")]
→ 找到 2 条相关录音：
  1. 「网络设备规划会议」(2026-07-17) - 摘要：...
  2. 「机房改造讨论」(2026-07-15) - 摘要：...
```

**获取详情：**
```
用户：第 1 个会议的待办事项是什么？
AI：[调用 get_recording_detail(recording_id=42)]
→ 待办事项：
  1. 确认交换机数量
  2. 提交机房布局方案
```

**列出最近会议：**
```
用户：最近有什么会议？
AI：[调用 list_recent_recordings(limit=5)]
→ 最近 5 场会议：
  1. 周会-项目进度同步 (2026-07-20)
  2. ...
```

## 配置说明

### 环境变量

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MCP_TOKEN` | (空) | MCP 端点认证 token，为空则不校验 |
| `MCP_MAX_RESULTS` | 20 | 搜索结果最大返回数 |

### 安全建议

- **内网部署**：MCP_TOKEN 可留空，依赖网络隔离
- **公网部署**：必须设置 MCP_TOKEN，防止未授权访问
- MCP 端点独立于业务 API 认证体系，使用单独的 token

## 技术细节

- 协议：MCP Streamable HTTP (JSON-RPC 2.0)
- 端点：`POST /mcp`
- 发现：`GET /mcp` 返回服务信息
- 搜索模式：
  - `keyword`：SQL LIKE 全文搜索（标题、摘要、转录文本、备注）
  - `semantic`：Zvec HNSW 向量检索（需配置 embedding 模型）
  - `hybrid`：全文 + 语义混合，加权排序
  - `auto`：有 embedding 模型时用 hybrid，否则降级为 keyword
