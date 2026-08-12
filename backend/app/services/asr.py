"""ASR 服务模块 - 调用 Qwen3-ASR（OpenAI 兼容 /v1/audio/transcriptions）。

ASR 服务实际接口规范：
- 端点: POST /v1/audio/transcriptions
- 请求参数: file（二进制）、response_format（默认 verbose_json）、enable_speaker_diarization（默认 true）、language（可选）、vocabulary_id（热词库）
- 不传 model 参数（服务端通过环境变量配置模型）
- verbose_json 响应字段: result(文本)、segments、duration、task_id、status、message
- segments 字段: text、start、end、speaker（直接使用这些字段名）

zh-recogn 接口规范：
- 端点: POST /api
- 请求参数: file（二进制，字段名 audio）
- 响应: {code: 0, msg: "ok", data: [{line, time: "HH:MM:SS,mmm --> HH:MM:SS,mmm", text}]}
- 不支持说话人识别
"""
import logging
import re
import aiofiles
import httpx
from app.config import settings

logger = logging.getLogger("meeting-transcriber.asr")

# 转录重试次数
MAX_RETRIES = 2


def _build_asr_headers() -> dict:
    """构建 ASR 请求头。如果配置了 X-NLS-Token 则携带。"""
    headers = {}
    token = settings.asr_xls_token
    if token:
        headers["X-NLS-Token"] = token
    return headers


async def transcribe_audio(
    file_path: str,
    filename: str,
    content_type: str,
    vocabulary_id: str | None = None,
) -> dict:
    """
    异步调用 ASR 服务转录音频文件。

    Args:
        vocabulary_id: 热词库字符串，格式如 "词汇1 权重1 词汇2 权重2"，None 表示不传。

    返回标准化字典:
    {
        "text": str,
        "segments": list,
        "duration": float,
        "language": str|None,
    }
    """
    async with aiofiles.open(file_path, "rb") as f:
        file_data = await f.read()

    # 分级超时：connect 30s，read 用配置值，write 300s（大文件上传），pool 10s
    timeout = httpx.Timeout(
        connect=30.0,
        read=float(settings.asr_timeout),
        write=300.0,
        pool=10.0,
    )

    files = {"file": (filename, file_data, content_type)}
    data = {
        "response_format": "verbose_json",
        "enable_speaker_diarization": "true",
    }
    if vocabulary_id:
        data["vocabulary_id"] = vocabulary_id
        logger.info(f"使用热词库: vocabulary_id={vocabulary_id[:100]}...")

    url = f"{settings.asr_base_url}/v1/audio/transcriptions"
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            logger.info(f"开始转录(attempt {attempt}/{MAX_RETRIES + 1}): filename={filename}, asr_url={settings.asr_base_url}")
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, files=files, data=data, headers=_build_asr_headers())
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
            # HTTP错误码不重试
            raise
        except Exception as e:
            # 其他异常不重试，直接抛出
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


# ===== zh-recogn 转录接口 =====

def _parse_zh_recogn_time(time_str: str) -> tuple[float, float]:
    """解析 zh-recogn 的时间格式 'HH:MM:SS,mmm --> HH:MM:SS,mmm'。"""
    match = re.match(
        r'(\d{2}):(\d{2}):(\d{2})[,.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,.](\d{3})',
        time_str.strip()
    )
    if not match:
        return 0.0, 0.0
    h1, m1, s1, ms1, h2, m2, s2, ms2 = match.groups()
    start = int(h1) * 3600 + int(m1) * 60 + int(s1) + int(ms1) / 1000
    end = int(h2) * 3600 + int(m2) * 60 + int(s2) + int(ms2) / 1000
    return start, end


async def transcribe_audio_zh_recogn(
    file_path: str,
    filename: str,
    content_type: str,
) -> dict:
    """
    调用 zh-recogn 接口转录音频文件。
    
    zh-recogn 不支持说话人识别，返回 SRT 格式的时间轴。
    
    返回标准化字典（与 transcribe_audio 一致）:
    {
        "text": str,
        "segments": list,
        "duration": float|None,
        "language": str|None,
    }
    """
    async with aiofiles.open(file_path, "rb") as f:
        file_data = await f.read()

    timeout = httpx.Timeout(
        connect=30.0,
        read=float(settings.zh_recogn_timeout),
        write=300.0,
        pool=10.0,
    )

    url = settings.zh_recogn_url
    files = {"audio": (filename, file_data, content_type)}

    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            logger.info(f"zh-recogn 转录开始(attempt {attempt}/{MAX_RETRIES + 1}): filename={filename}, url={url}")
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, files=files)
                resp.raise_for_status()
                raw = resp.json()
            break
        except (httpx.RemoteProtocolError, httpx.ReadError, httpx.ConnectError) as e:
            last_error = e
            logger.warning(f"zh-recogn 转录连接异常(attempt {attempt}): {e}")
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
        raise RuntimeError("zh-recogn 转录失败：未知原因")

    # 检查返回格式
    if raw.get("code") != 0:
        raise RuntimeError(f"zh-recogn 返回错误: code={raw.get('code')}, msg={raw.get('msg')}")

    data_list = raw.get("data") or []
    normalized_segments = []
    text_parts = []
    max_end = 0.0

    for item in data_list:
        time_str = item.get("time", "")
        start, end = _parse_zh_recogn_time(time_str)
        text = item.get("text", "").strip()
        if not text:
            continue
        normalized_segments.append({
            "text": text,
            "start": start,
            "end": end,
            "speaker": None,  # zh-recogn 不支持说话人识别
        })
        text_parts.append(text)
        if end > max_end:
            max_end = end

    text = " ".join(text_parts)
    duration = max_end if max_end > 0 else None

    logger.info(
        f"zh-recogn 转录完成: filename={filename}, duration={duration}, "
        f"segments={len(normalized_segments)}, text_len={len(text)}"
    )

    return {
        "text": text,
        "segments": normalized_segments,
        "duration": duration,
        "language": "zh",  # zh-recogn 是中文识别接口
    }


# ===== 声纹管理 API =====

async def list_voiceprint_speakers() -> list[dict]:
    """获取 ASR 后端已注册的声纹说话人列表。"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(f"{settings.asr_base_url}/api/v1/voiceprint-speakers", headers=_build_asr_headers())
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
) -> dict:
    """在 ASR 后端创建说话人并注册声纹样本。"""
    async with httpx.AsyncClient(timeout=60) as client:
        files = {"file": (filename, file_data, content_type)}
        data = {"display_name": display_name}
        if description:
            data["description"] = description
        resp = await client.post(
            f"{settings.asr_base_url}/api/v1/voiceprint-speakers",
            files=files,
            data=data,
            headers=_build_asr_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def add_voiceprint_samples(
    speaker_id: str,
    files_data: list[tuple[bytes, str, str]],
) -> dict:
    """向已有说话人添加声纹样本。files_data: [(bytes, filename, content_type), ...]"""
    async with httpx.AsyncClient(timeout=60) as client:
        files = [
            ("file", (fname, fdata, ftype))
            for fdata, fname, ftype in files_data
        ]
        resp = await client.post(
            f"{settings.asr_base_url}/api/v1/voiceprint-speakers/{speaker_id}/samples",
            files=files,
            headers=_build_asr_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def delete_voiceprint_speaker(speaker_id: str) -> dict:
    """从 ASR 后端删除说话人。"""
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.delete(
            f"{settings.asr_base_url}/api/v1/voiceprint-speakers/{speaker_id}",
            headers=_build_asr_headers(),
        )
        resp.raise_for_status()
        return resp.json()
