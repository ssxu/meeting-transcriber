"""Pydantic 响应模型定义。"""
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field, ConfigDict, model_validator


class RecordingOut(BaseModel):
    """录音列表项响应模型。"""
    id: int
    title: str
    original_filename: str
    file_size: int
    duration: Optional[float] = None
    language: Optional[str] = None
    status: str
    keywords: Optional[list] = None
    tags: Optional[list] = None
    is_shared: bool = False
    created_at: datetime
    hotword_library_id: Optional[int] = None
    meeting_type_id: Optional[int] = None
    content_category: Optional[str] = None
    content_sub_type: Optional[str] = None

    model_config = {"from_attributes": True}


class RecordingDetail(RecordingOut):
    """录音详情响应模型。"""
    transcript_text: Optional[str] = None
    transcript_segments: Optional[list] = None
    summary_text: Optional[str] = None
    mindmap_text: Optional[str] = None
    action_items: Optional[list] = None
    error_message: Optional[str] = None
    share_token: Optional[str] = None
    notes: Optional[str] = None


class SearchResult(BaseModel):
    """搜索结果响应模型。"""
    id: int
    title: str
    original_filename: str
    status: str
    snippet: str
    created_at: Optional[str] = None
    score: Optional[float] = None
    tags: Optional[list] = None
    speakers: Optional[list] = None
    has_transcript: Optional[bool] = None

    model_config = {"from_attributes": True}


class StatsOut(BaseModel):
    """统计数据响应模型。"""
    total: int
    total_duration: float
    status_breakdown: dict
    recent_7d_count: int
    recent_7d_duration: float


class BatchDeleteRequest(BaseModel):
    """批量删除请求模型。"""
    ids: list[int]


class BatchExportRequest(BaseModel):
    """批量导出请求模型。"""
    ids: list[int]


class RenameRequest(BaseModel):
    """重命名请求模型。"""
    title: str


class ShareResponse(BaseModel):
    """分享链接响应模型。"""
    share_url: str
    share_token: str


# ===== 热词库 =====
class HotwordCreate(BaseModel):
    word: str = Field(..., max_length=100)
    weight: int = Field(1, ge=1, le=100)


class HotwordOut(BaseModel):
    id: int
    library_id: int
    word: str
    weight: int
    created_at: datetime
    model_config = {"from_attributes": True}


class HotwordLibraryCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    is_default: bool = False


class HotwordLibraryUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    is_default: Optional[bool] = None


class HotwordLibraryOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_default: bool = False
    hotword_count: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}


class HotwordLibraryDetail(HotwordLibraryOut):
    hotwords: list[HotwordOut] = []


class HotwordBatchAdd(BaseModel):
    """批量添加热词。"""
    hotwords: list[HotwordCreate] = []


class HotwordImportText(BaseModel):
    """从文本导入热词，每行一个词，格式: 词 权重(可选)。"""
    text: str


# ===== 会议类型 =====
class MeetingTypeCreate(BaseModel):
    name: str = Field(..., max_length=200)
    description: Optional[str] = None
    category: str = "meeting"  # meeting / learning
    sub_type: str = "regular"  # regular/tech_review/.../course/podcast/academic
    map_prompt: str = ""
    reduce_prompt: str = ""
    single_extract_prompt: str = ""
    is_default: bool = False


class MeetingTypeUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    sub_type: Optional[str] = None
    map_prompt: Optional[str] = None
    reduce_prompt: Optional[str] = None
    single_extract_prompt: Optional[str] = None
    is_default: Optional[bool] = None


class MeetingTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    category: str = "meeting"
    sub_type: str = "regular"
    map_prompt: str = ""
    reduce_prompt: str = ""
    single_extract_prompt: str = ""
    summary_prompt: str = ""  # 兼容旧字段
    is_default: bool = False
    is_builtin: bool = False
    created_at: datetime
    model_config = {"from_attributes": True}


# ===== 声纹管理 =====
class VoiceprintSpeakerCreate(BaseModel):
    display_name: str = Field(..., max_length=200)
    description: Optional[str] = None


class VoiceprintSpeakerOut(BaseModel):
    id: int
    speaker_id: str
    display_name: str
    description: Optional[str] = None
    voiceprint_count: int = 0
    created_at: datetime
    model_config = {"from_attributes": True}


class VoiceprintSpeakerUpdate(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None


# ===== 重新生成请求 =====
class RegenerateRequest(BaseModel):
    """重新生成请求 - 可指定会议类型、细分类型和模型。"""
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")
    meeting_type_id: Optional[int] = None
    model_id: Optional[int] = None
    sub_type: Optional[str] = None  # 细分类型，如 regular/course/podcast 等


class RegenerateActionItemsRequest(BaseModel):
    """重新生成待办事项请求。"""
    model_config = ConfigDict(protected_namespaces=())
    model_id: Optional[int] = None


class RegenerateKeywordsRequest(BaseModel):
    """重新生成关键词与标签请求。"""
    model_config = ConfigDict(protected_namespaces=())
    model_id: Optional[int] = None


class RegenerateMindmapRequest(BaseModel):
    """重新生成思维导图请求。"""
    model_config = ConfigDict(protected_namespaces=())
    model_id: Optional[int] = None


# ===== LLM 模型管理 =====
class LLMModelCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str = Field(..., max_length=200)
    model_id: str = Field(..., max_length=200)
    base_url: str = Field(..., max_length=500)
    api_key: str = ""
    model_type: str = "chat"  # chat / embedding / rerank
    is_default: bool = False
    is_enabled: bool = True
    sort_order: int = 0
    max_context_length: int = 32000
    description: Optional[str] = None


class LLMModelUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: Optional[str] = None
    model_id: Optional[str] = None
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model_type: Optional[str] = None
    is_default: Optional[bool] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    max_context_length: Optional[int] = None
    description: Optional[str] = None


class LLMModelOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)
    id: int
    name: str
    model_id: str
    base_url: str
    api_key: str = ""
    model_type: str = "chat"
    is_default: bool = False
    is_enabled: bool = True
    sort_order: int = 0
    max_context_length: int = 32000
    description: Optional[str] = None
    created_at: Optional[datetime] = None  # 允许 None，from_model 中兜底

    @classmethod
    def from_model(cls, model):
        """从 LLMModel ORM 对象创建响应模型，对 api_key 脱敏。"""
        data = {}
        for field in cls.model_fields:
            if hasattr(model, field):
                val = getattr(model, field, None)
                # created_at 兜底，防止 db.refresh 后仍为 None 导致 Pydantic 验证失败
                if field == "created_at" and val is None:
                    from datetime import datetime, timezone
                    val = datetime.now(timezone.utc)
                data[field] = val
        # 脱敏：只返回掩码
        key = data.get("api_key", "")
        if key and len(key) > 8:
            data["api_key"] = key[:4] + "****" + key[-4:]
        elif key:
            data["api_key"] = "****"
        return cls(**data)

    @model_validator(mode="after")
    def _mask_api_key(self):
        """防御性脱敏：如果 api_key 看起来像真实密钥（非掩码格式），自动掩码。"""
        key = self.api_key
        if key and "****" not in key and len(key) > 8:
            object.__setattr__(self, "api_key", key[:4] + "****" + key[-4:])
        elif key and "****" not in key and key != "":
            object.__setattr__(self, "api_key", "****")
        return self


# ===== ASR 提供商管理 =====
class AsrProviderCreate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str = Field(..., max_length=200)
    base_url: str = Field(..., max_length=500)
    auth_header: str = ""
    auth_value: str = ""
    timeout: int = 600
    supports_speaker: bool = True
    supports_hotwords: bool = True
    is_default: bool = False
    is_enabled: bool = True
    sort_order: int = 0
    description: Optional[str] = None


class AsrProviderUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: Optional[str] = None
    base_url: Optional[str] = None
    auth_header: Optional[str] = None
    auth_value: Optional[str] = None
    timeout: Optional[int] = None
    supports_speaker: Optional[bool] = None
    supports_hotwords: Optional[bool] = None
    is_default: Optional[bool] = None
    is_enabled: Optional[bool] = None
    sort_order: Optional[int] = None
    description: Optional[str] = None


class AsrProviderOut(BaseModel):
    model_config = ConfigDict(protected_namespaces=(), from_attributes=True)
    id: int
    name: str
    base_url: str
    auth_header: str = ""
    auth_value: str = ""
    timeout: int = 600
    supports_speaker: bool = True
    supports_hotwords: bool = True
    is_default: bool = False
    is_enabled: bool = True
    sort_order: int = 0
    description: Optional[str] = None
    created_at: Optional[datetime] = None


# ===== 搜索 =====
class SearchHit(BaseModel):
    """统一搜索结果项（全文 + 语义）。"""
    id: int
    title: str
    original_filename: str
    status: str
    snippet: str  # 已高亮的 HTML
    created_at: datetime
    score: float = 0.0  # 相关度分数
    tags: Optional[list] = None
    speakers: Optional[list] = None
    has_transcript: bool = False


# ===== 增强统计 =====
class SpeakerStat(BaseModel):
    """单个说话人统计。"""
    speaker: str
    total_speak_time: float
    speak_count: int
    avg_segment_length: float
    percentage: float


class MeetingAnalysisOut(BaseModel):
    """会议效率分析响应。"""
    rec_id: int
    title: str
    total_duration: float
    speaker_count: int
    speaker_stats: list[SpeakerStat]
    participation_rate: float


class SpeakerSummaryOut(BaseModel):
    """说话人维度统计响应。"""
    speaker: str
    total_meetings: int
    total_speak_time: float
    total_segments: int
    avg_speak_time_per_meeting: float


class EnhancedStatsOut(BaseModel):
    """增强统计响应。"""
    total: int
    total_duration: float
    status_breakdown: dict
    recent_7d_count: int
    recent_7d_duration: float
    date_range: Optional[dict] = None
    speaker_count: int = 0
    avg_duration: float = 0
    tag_stats: Optional[list[dict]] = None  # [{"tag": "xxx", "count": N}, ...]


# ===== 转录稿编辑 =====
class TranscriptSegmentEdit(BaseModel):
    """单个转录段落的编辑。"""
    index: int  # 段落索引
    text: Optional[str] = None  # 新文本（None表示不修改）
    speaker: Optional[str] = None  # 新说话人（None表示不修改）
    start: Optional[float] = None  # 新开始时间
    end: Optional[float] = None  # 新结束时间


class TranscriptEditRequest(BaseModel):
    """逐字稿编辑请求。"""
    segments: Optional[list[dict]] = None  # 完整替换segments
    patch: Optional[list[TranscriptSegmentEdit]] = None  # 增量修改
    speaker_names: Optional[dict[str, str]] = None  # 说话人重命名映射 {old_name: new_name}


class SegmentMergeRequest(BaseModel):
    """段落合并请求。"""
    indices: list[int]  # 要合并的段落索引列表（按顺序）
    speaker: Optional[str] = None  # 合并后的说话人


class SegmentSplitRequest(BaseModel):
    """段落拆分请求。"""
    index: int  # 要拆分的段落索引
    position: int  # 在文本的第几个字符处拆分
    speaker: Optional[str] = None  # 拆分后第二段的说话人


class ExportFormatRequest(BaseModel):
    """导出格式请求。"""
    format: str = "txt"  # txt / srt / vtt / md / pdf / docx
    with_timestamps: bool = True
    with_speakers: bool = True


# ===== 转录队列 =====
class TranscriptionQueueOut(BaseModel):
    """转录队列项响应模型。"""
    id: int
    recording_id: int
    status: str
    engine: str = "qwen_asr"
    queued_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    # 冗余字段，方便前端展示
    recording_title: Optional[str] = None
    recording_filename: Optional[str] = None
    recording_status: Optional[str] = None
    recording_duration: Optional[float] = None
    model_config = {"from_attributes": True}


class TranscriptionScheduleOut(BaseModel):
    """转录时间设置响应模型。"""
    enabled: bool = False
    start_time: str = "00:00"  # HH:MM
    end_time: str = "23:59"  # HH:MM


class TranscriptionScheduleUpdate(BaseModel):
    """转录时间设置更新模型。"""
    enabled: bool = False
    start_time: str = "00:00"
    end_time: str = "23:59"


class UploadSrtRequest(BaseModel):
    """SRT 上传请求。"""
    # 实际用 UploadFile，这里不建模
    pass


# ===== 提示词配置 =====
class PromptConfigOut(BaseModel):
    """提示词配置响应模型。"""
    id: int
    prompt_type: str  # map / reduce / single_extract
    content: str = ""
    description: Optional[str] = None
    is_enabled: bool = True
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


class PromptConfigUpdate(BaseModel):
    """提示词配置更新模型。"""
    content: Optional[str] = None
    description: Optional[str] = None
    is_enabled: Optional[bool] = None


class PromptConfigReset(BaseModel):
    """提示词配置重置模型 - 重置为默认值。"""
    prompt_type: str  # map / reduce / single_extract


# ===== AI 对话 =====
class ChatSource(BaseModel):
    """对话来源引用——检索到的原文段落。"""
    segment_index: int
    text: str
    start: Optional[float] = None
    end: Optional[float] = None
    speaker: Optional[str] = None


class ChatRequest(BaseModel):
    """AI 对话请求。"""
    model_config = ConfigDict(protected_namespaces=())
    message: str
    model_id: Optional[int] = None
    history: Optional[list[dict]] = None


class ChatResponse(BaseModel):
    """AI 对话响应。"""
    reply: str
    sources: list[ChatSource] = []
