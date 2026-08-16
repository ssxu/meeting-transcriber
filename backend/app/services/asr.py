"""ASR 服务模块 - 支持多提供商配置的语音识别服务。

ASR 服务接口规范（OpenAI 兼容 /v1/audio/transcriptions）:
- 端点: POST /v1/audio/transcriptions
- 请求参数: file（二进制）、response_format（verbose_json）、enable_speaker_diarization、language、vocabulary_id
- verbose_json 响应字段: result(text)、segments、duration、task_id、status、message
- segments 字段: text、start、end、speaker
"""
import logging
import aiofiles
import httpx
from app.config import settings
from app.routes.asr_providers import resolve_asr_provider

logger = logging.getLogger("meeting-transcriber.asr")

# 转录重试次数
MAX_RETRIES = 2


def _build_provider_headers(provider) -> dict:
    """根据提供商配置构建请求头。"""
    headers = {}
    if provider.auth_header and provider.auth_value:
        headers[provider.auth_header] = provider.auth_value
    return headers


async def transcribe_audio(
    file_path: str,
    filename: str,
    content_type: str,
    provider_id: int | None = None,
    vocabulary_id: str | None = None,
    db=None,
) -> dict:
    """
    异步调用 ASR 服务转录音频文件。

    Args:
        file_path: 音频文件路径
        filename: 原始文件名
        content_type: 文件 MIME 类型
        provider_id: ASR 提供商 ID（None 表示自动选择）
        vocabulary_id: 热词库字符串，格式如 "词汇1 权重1 词汇2 权重2"
        db: AsyncSession（用于从数据库选择提供商）

    返回标准化字典:
    {
        "text": str,
        "segments": list,
        "duration": float,
        "language": str|None,
    }
    """
    # 解析 ASR 提供商配置
    provider = None
    if db is not None:
        provider = await resolve_asr_provider(provider_id, db)

    # 构建请求 URL 和 headers
    if provider:
        base_url = provider.base_url.rstrip("/")
        headers = _build_provider_headers(provider)
        timeout = httpx.Timeout(
            connect=30.0,
            read=float(provider.timeout),
            write=300.0,
            pool=10.0,
        )
    else:
        # 回退到全局环境变量配置
        base_url = settings.asr_base_url.rstrip("/")
        headers = _build_legacy_headers()
        timeout = httpx.Timeout(
            connect=30.0,
            read=float(settings.asr_timeout),
            write=300.0,
            pool=10.0,
        )

    url = f"{base_url}/v1/audio/transcriptions"

    async with aiofiles.open(file_path, "rb") as f:
        file_data = await f.read()

    files = {"file": (filename, file_data, content_type)}
    data = {
        "response_format": "verbose_json",
        "enable_speaker_diarization": "true",
    }

    if vocabulary_id:
        data["vocabulary_id"] = vocabulary_id
        logger.info(f"使用热词库: vocabulary_id={vocabulary_id[:100]}...")

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            logger.info(f"开始转录(attempt {attempt}/{MAX_RETRIES + 1}): filename={filename}, asr_url={base_url}")
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, files=files, data=data, headers=headers)
                resp.raise_for_status()
                raw = resp.json()
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            last_error = e
            logger.warning(f"转录连接异常(attempt {attempt}): {e}")
            if attempt <= MAX_RETRIES:
                import asyncio
                await asyncio.sleep(3 * attempt)
            else:
                raise
        except httpx.HTTPStatusError as e:
            raise
        except Exception as e:
            last_error = e
            raise
    else:
        if last_error:
            raise last_error
        raise RuntimeError("转录失败：未知原因")

    text = raw.get("result") or raw.get("text") or ""
    duration = raw.get("duration")
    raw_segments = raw.get("segments") or []

    normalized_segments = []
    for seg in raw_segments:
        normalized_segments.append({
            "text": seg.get("text", ""),
            "start": seg.get("start"),
            "end": seg.get("end"),
            "speaker": seg.get("speaker"),
        })

    language = raw.get("language")

    logger.info(
        f"转录完成: filename={filename}, duration={duration}, "
        f"segments={len(normalized_segments)}, text_len={len(text)}"
    )

    return {
        "text": text,
        "segments": normalized_segments,
        "duration": duration,
        "language": language,
    }


def _build_legacy_headers() -> dict:
    """构建兼容旧环境的 ASR 请求头（从环境变量读取）。"""
    headers = {}
    token = settings.asr_xls_token
    if token:
        headers["X-NLS-Token"] = token
    return headers


# ===== 声纹管理 API（使用默认或首个启用的提供商） =====

async def list_voiceprint_speakers(db=None) -> list[dict]:
    """获取 ASR 后端已注册的声纹说话人列表。"""
    provider = await resolve_asr_provider(None, db) if db else None
    base_url = provider.base_url if provider else settings.asr_base_url
    headers = _build_provider_headers(provider) if provider else _build_legacy_headers()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{base_url}/api/v1/voiceprint-speakers", headers=headers)
        resp.raise_for_status()
        raw = resp.json()
    speakers = raw.get("speakers", [])
    return [
        {
            "speaker_id": s.get("speaker_id", ""),
            "display_name": s.get("display_name", ""),
            "description": s.get("description"),
            "voiceprint_count": s.get("voiceprint_count", 0),
        }
        for s in speakers
    ]


async def create_voiceprint_speaker(
    display_name: str,
    file_data: bytes,
    filename: str,
    content_type: str,
    description: str | None = None,
    db=None,
) -> dict:
    """在 ASR 后端创建说话人并注册声纹样本。"""
    provider = await resolve_asr_provider(None, db) if db else None
    base_url = provider.base_url if provider else settings.asr_base_url
    headers = _build_provider_headers(provider) if provider else _build_legacy_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        files = {"file": (filename, file_data, content_type)}
        data = {"display_name": display_name}
        if description:
            data["description"] = description
        resp = await client.post(
            f"{base_url}/api/v1/voiceprint-speakers",
            files=files,
            data=data,
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


async def add_voiceprint_samples(
    speaker_id: str,
    files_data: list[tuple[bytes, str, str]],
    db=None,
) -> dict:
    """向已有说话人添加声纹样本。files_data: [(bytes, filename, content_type), ...]"""
    provider = await resolve_asr_provider(None, db) if db else None
    base_url = provider.base_url if provider else settings.asr_base_url
    headers = _build_provider_headers(provider) if provider else _build_legacy_headers()
    async with httpx.AsyncClient(timeout=60) as client:
        files = [
            ("file", (fname, fdata, ftype))
            for fdata, fname, ftype in files_data
        ]
        resp = await client.post(
            f"{base_url}/api/v1/voiceprint-speakers/{speaker_id}/samples",
            files=files,
            data={},
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()


async def delete_voiceprint_speaker(speaker_id: str, db=None) -> dict:
    """从 ASR 后端删除说话人。"""
    provider = await resolve_asr_provider(None, db) if db else None
    base_url = provider.base_url if provider else settings.asr_base_url
    headers = _build_provider_headers(provider) if provider else _build_legacy_headers()
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{base_url}/api/v1/voiceprint-speakers/{speaker_id}",
            headers=headers,
        )
        resp.raise_for_status()
        return resp.json()