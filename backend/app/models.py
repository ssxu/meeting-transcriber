"""数据模型定义模块。"""
from datetime import datetime
from sqlalchemy import String, Integer, Float, Text, JSON, func, Boolean, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from app.db import UTCDateTime


class Base(DeclarativeBase):
    pass


class Recording(Base):
    """录音记录模型。"""
    __tablename__ = "recordings"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 录音标题（可编辑，默认为文件名）
    title: Mapped[str] = mapped_column(String(500), default="")
    original_filename: Mapped[str] = mapped_column(String(500))
    stored_filename: Mapped[str] = mapped_column(String(200))
    audio_path: Mapped[str] = mapped_column(String(500))
    # 转录文本文件路径
    transcript_path: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    # 摘要文本文件路径
    summary_path: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    content_type: Mapped[str] = mapped_column(String(100), default="audio/mpeg")
    file_size: Mapped[int] = mapped_column(Integer, default=0)
    duration: Mapped[float | None] = mapped_column(Float, nullable=True, default=None)
    language: Mapped[str | None] = mapped_column(String(20), nullable=True, default=None)
    # pending -> transcribing -> transcribed -> summarizing -> done / error
    status: Mapped[str] = mapped_column(String(30), default="pending")
    transcript_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    transcript_segments: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 思维导图 Markdown 文本
    mindmap_text: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 从 LLM 提取的关键词（JSON 数组）
    keywords: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # 从 LLM 提取的标签（JSON 数组，转录完成后自动生成）
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    # 从 LLM 提取的待办事项（JSON 数组）
    action_items: Mapped[list | None] = mapped_column(JSON, nullable=True, default=None)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 是否已分享（只读访问）
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)
    # 分享 token
    share_token: Mapped[str | None] = mapped_column(String(64), nullable=True, default=None)
    # 关联的热词库 ID（可选，上传时选择）
    hotword_library_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("hotword_libraries.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # 关联的会议类型 ID（可选，上传时选择）
    meeting_type_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("meeting_types.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # 内容场景大类: meeting(会议沟通类) / learning(学习知识类)
    content_category: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    # 内容场景细分类型: regular/tech_review/.../course/podcast/academic
    content_sub_type: Mapped[str | None] = mapped_column(String(50), nullable=True, default=None)
    # 转录引擎: qwen_asr
    engine: Mapped[str] = mapped_column(String(30), default="qwen_asr")
    # 关联的 ASR 提供商 ID
    asr_provider_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("asr_providers.id", ondelete="SET NULL"), nullable=True, default=None
    )
    # 用户备注（Markdown 格式）
    notes: Mapped[str | None] = mapped_column(Text, nullable=True, default=None)
    # 时间戳
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class HotwordLibrary(Base):
    """热词库模型。"""
    __tablename__ = "hotword_libraries"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class Hotword(Base):
    """热词条目模型。"""
    __tablename__ = "hotwords"

    id: Mapped[int] = mapped_column(primary_key=True)
    library_id: Mapped[int] = mapped_column(Integer, ForeignKey("hotword_libraries.id", ondelete="CASCADE"))
    word: Mapped[str] = mapped_column(String(100))
    weight: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())


class MeetingType(Base):
    """会议类型模型 - 管理录音类型及对应的 LLM 提示词模板（Map/Reduce/Single-Extract）。
    
    category: 大类 (meeting=会议沟通类 / learning=学习知识类)
    sub_type: 细分类型 (regular/tech_review/.../course/podcast/academic)
    map_prompt: Map 阶段提示词（逐块提取）
    reduce_prompt: Reduce 阶段提示词（全局汇总）
    single_extract_prompt: 单次提取提示词（短文本直接提取）
    """
    __tablename__ = "meeting_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    # 大类: meeting(会议沟通类) / learning(学习知识类)
    category: Mapped[str] = mapped_column(String(50), default="meeting")
    # 细分类型: regular/tech_review/product_req/executive/product_intro/tech_exchange/research/course/podcast/academic
    sub_type: Mapped[str] = mapped_column(String(50), default="regular")
    # Map 阶段提示词（逐块提取结构化信息）
    map_prompt: Mapped[str] = mapped_column(Text, default="")
    # Reduce 阶段提示词（全局汇总为完整纪要）
    reduce_prompt: Mapped[str] = mapped_column(Text, default="")
    # 单次提取提示词（短文本不分片时直接提取）
    single_extract_prompt: Mapped[str] = mapped_column(Text, default="")
    # 兼容旧字段（保留但不再使用）
    summary_prompt: Mapped[str] = mapped_column(Text, default="")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # 是否为系统内置类型（初始化时创建，不可删除）
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class VoiceprintSpeaker(Base):
    """声纹说话人模型 - 本地管理说话人信息，与ASR后端声纹系统同步。"""
    __tablename__ = "voiceprint_speakers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # ASR 后端返回的持久化 speaker_id
    speaker_id: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    voiceprint_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class LLMModel(Base):
    """LLM 模型管理 - 管理可调用的大语言模型配置。
    model_type 区分: chat(对话) / embedding(向量) / rerank(重排序)
    """
    __tablename__ = "llm_models"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # 模型标识（发送给 API 的 model 字段）
    model_id: Mapped[str] = mapped_column(String(200))
    # API Base URL
    base_url: Mapped[str] = mapped_column(String(500))
    # API Key
    api_key: Mapped[str] = mapped_column(String(500), default="")
    # 模型类型: chat / embedding / rerank
    model_type: Mapped[str] = mapped_column(String(30), default="chat")
    # 是否为默认模型（同类型内只能有一个默认）
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    # 是否启用
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 排序权重
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    # 最大上下文 token 数（用于分片调用）
    max_context_length: Mapped[int] = mapped_column(Integer, default=32000)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class TranscriptionQueue(Base):
    """转录队列模型 - 管理待转录任务。"""
    __tablename__ = "transcription_queue"

    id: Mapped[int] = mapped_column(primary_key=True)
    recording_id: Mapped[int] = mapped_column(ForeignKey("recordings.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(30), default="queued")  # queued / processing / done / cancelled
    # 转录引擎: qwen_asr (默认)
    engine: Mapped[str] = mapped_column(String(30), default="qwen_asr")
    queued_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, default=None)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(), nullable=True, default=None)


class SystemSetting(Base):
    """系统设置模型 - 存储键值对配置。"""
    __tablename__ = "system_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


class PromptConfig(Base):
    """会议纪要提示词配置 - 管理 Map/Reduce 等提示词模板。

    prompt_type 区分: map / reduce / single_extract
    用户可在前端修改这些提示词，未配置时使用 config.py 中的默认值。
    """
    __tablename__ = "prompt_configs"

    id: Mapped[int] = mapped_column(primary_key=True)
    prompt_type: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # map / reduce / single_extract
    content: Mapped[str] = mapped_column(Text, default="")
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )


class AsrProvider(Base):
    """ASR 提供商配置 - 管理 OpenAI 兼容的语音识别服务。"""
    __tablename__ = "asr_providers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    base_url: Mapped[str] = mapped_column(String(500))
    auth_header: Mapped[str] = mapped_column(String(100), default="")
    auth_value: Mapped[str] = mapped_column(String(500), default="")
    timeout: Mapped[int] = mapped_column(Integer, default=600)
    supports_speaker: Mapped[bool] = mapped_column(Boolean, default=True)
    supports_hotwords: Mapped[bool] = mapped_column(Boolean, default=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True, default=None)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(), server_default=func.now(), onupdate=func.now()
    )

