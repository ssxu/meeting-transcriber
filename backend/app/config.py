"""应用配置模块 - 读取环境变量并提供全局配置。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置，从 .env 文件或环境变量读取。"""
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 数据库（Docker 部署时用 /data/meeting.db，本地开发用 ./data/meeting.db）
    database_url: str = "sqlite+aiosqlite:///./data/meeting.db"

    # 录音文件存储根目录（下设 recordings / transcripts / summaries 子目录）
    # Docker 部署时用 /data，本地开发默认用 ./data
    storage_path: str = "./data"

    # ===== Qwen3-ASR（OpenAI 兼容）=====
    # ASR 服务地址（实际部署在 192.168.100.51:7034）
    asr_base_url: str = "http://192.168.100.51:7034"
    asr_model: str = "qwen3-asr"  # 保留字段但 ASR 服务不使用此参数
    asr_timeout: int = 600
    # X-NLS-Token 安全访问令牌（可选，配置了就发送，不配置则不发送）
    asr_xls_token: str = ""

    # ===== 本地 LLM（OpenAI 兼容 chat/completions）=====
    llm_base_url: str = "http://host.docker.internal:11434"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen3:latest"
    llm_timeout: int = 300
    # 全局默认最大上下文长度（当数据库无默认模型时使用）
    llm_max_context_length: int = 32000

    # 向量数据库（Zvec）数据存储路径
    zvec_data_path: str = "./data/zvec"

    # CORS 允许的来源（逗号分隔），* 表示全部允许
    cors_origins: str = "*"

    # Webhook 回调 URL（可选，转录完成后通知）
    webhook_url: str = ""

    # 分享链接 token 密钥（用于生成只读分享链接）
    share_secret: str = "meeting-transcriber-share-secret-2024"

    # ===== 登录认证 =====
    # 登录密码，局域网部署时建议修改
    auth_password: str = "password"
    # JWT 密钥
    jwt_secret: str = "meeting-transcriber-jwt-secret-2024"
    # Token 有效期（小时）
    jwt_expire_hours: int = 72

    # ===== 时区 =====
    # 影响日志时间、文件目录日期、导出文档中的时间显示
    # 字段名 tz 会映射到环境变量 TZ（docker-compose.yml 中设置），默认 Asia/Shanghai
    tz: str = "Asia/Shanghai"

    @property
    def timezone(self) -> str:
        return self.tz

    # ===== MCP Server =====
    # MCP 端点认证 token，为空则不启用 token 校验（仅限内网部署）
    mcp_token: str = ""
    # MCP 搜索结果最大返回数
    mcp_max_results: int = 20


# ===== 会议纪要默认提示词 =====
# Map 阶段：单个文本块的结构化提取
DEFAULT_MAP_PROMPT = """你是一个会议分析助手。请分析以下会议逐字稿片段，提取核心要素。

【片段时间范围】{time_range}

请严格按以下 JSON 格式输出（不要 markdown 代码块）：
{{
  "topics": ["本段讨论的主要议题及其要点"],
  "decisions": ["本段明确达成的决议事项"],
  "action_items": ["明确的待办任务，需包含执行人、任务内容、截止时间（如有）"],
  "summary": "本段的简要概述，2-3句话"
}}

要求：
- 使用中文
- 只提取逐字稿中明确存在的信息，不要编造
- 如果某类要素在本段不存在，返回空数组
- action_items 尽量包含具体执行人

===== 会议逐字稿片段 =====
"""

# Reduce 阶段：全局汇总
DEFAULT_REDUCE_PROMPT = """你是一个专业的会议纪要生成助手。以下是一个长会议按时间顺序分片提取的结构化摘要清单。

请将这些片段摘要整合为一份完整的会议纪要，执行以下操作：
1. **议题合并**：把跨多个时间段重复讨论的同一议题合并归类，梳理出完整的讨论脉络
2. **待办智能覆盖**：消除重复待办。如果后文对前文提到的任务或负责人进行了修改，以靠后的最终决策为准
3. **格式化输出**：按以下模板生成 Markdown 文档

输出模板：
## 会议速览
（一段话概括会议主题和主要结论）

## 议题详情
### 议题一：xxx
- 讨论要点...
### 议题二：xxx
- 讨论要点...

## 决议列表
1. 决议一
2. 决议二

## 待办追踪表
| 任务 | 负责人 | 截止时间 | 备注 |
|------|--------|----------|------|
| xxx  | xxx    | xxx      | xxx  |

要求：
- 使用中文，条理清晰
- 合并重复内容，保持逻辑连贯
- 不要编造内容
- 待办追踪表如果没有信息则留空单元格

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "完整的会议纪要 Markdown 文本（按上述模板）",
  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]
}}

===== 按时间顺序排列的各片段结构化摘要 =====
"""

# 不分片时的单次提取提示词
DEFAULT_SINGLE_EXTRACT_PROMPT = """你是一个会议分析助手。请从以下会议逐字稿中提取信息，以 JSON 格式返回。

返回格式要求（严格 JSON，不要 markdown 代码块）：
{{
  "summary": "结构化的会议纪要 Markdown 文本，包含：会议速览、议题详情、决议列表、待办追踪表",
  "action_items": ["待办事项1（含负责人和截止时间）", "待办事项2"]
}}

要求：
- summary: 按会议速览、议题详情、决议列表、待办追踪表的模板组织
- action_items: 从纪要中提取的结构化待办事项列表
- 使用中文
- 不要编造逐字稿中不存在的内容

===== 会议逐字稿 =====
"""


settings = Settings()
