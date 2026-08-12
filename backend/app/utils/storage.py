"""文件存储工具模块 - 录音文件、转录文本、摘要文本的落盘管理。

文件按日期目录组织：storage_path/category/YYYY/MM/DD/filename
"""
import os
import logging
import uuid
from datetime import datetime, timezone

import aiofiles
import httpx

from app.config import settings
from app.utils.timezone import get_app_tz

logger = logging.getLogger("meeting-transcriber.storage")


def _ensure_dir(path: str):
    """确保目录存在。"""
    os.makedirs(path, exist_ok=True)


def get_subdir(category: str) -> str:
    """获取顶层存储子目录路径（向后兼容）。

    category: recordings / transcripts / summaries
    返回 storage_path/category/，并确保目录存在。
    """
    path = os.path.join(settings.storage_path, category)
    _ensure_dir(path)
    return path


def get_dated_subdir(category: str, date_obj: datetime | None = None) -> str:
    """获取按日期组织的存储子目录路径。

    生成 storage_path/category/YYYY/MM/DD/ 目录结构。
    当 date_obj 为 None 时使用当前时间。
    返回完整路径，并确保目录存在。
    """
    if date_obj is None:
        date_obj = datetime.now(get_app_tz())
    year = f"{date_obj.year:04d}"
    month = f"{date_obj.month:02d}"
    day = f"{date_obj.day:02d}"
    path = os.path.join(settings.storage_path, category, year, month, day)
    _ensure_dir(path)
    return path


async def save_upload(file) -> tuple[str, str, int]:
    """
    保存上传的音频文件，返回 (存储文件名, 完整路径, 字节大小)。
    文件保存到 storage_path/recordings/YYYY/MM/DD/ 子目录。
    """
    # Bug 修复: 扩展名为空时使用 .wav 而非空字符串
    filename = file.filename or ""
    ext = os.path.splitext(filename)[1]
    if not ext:
        ext = ".wav"
    ext = ext.lower()

    stored_name = f"{uuid.uuid4().hex}{ext}"
    recordings_dir = get_dated_subdir("recordings")
    path = os.path.join(recordings_dir, stored_name)

    size = 0
    async with aiofiles.open(path, "wb") as f:
        while chunk := await file.read(1024 * 1024):
            await f.write(chunk)
            size += len(chunk)
    logger.info(f"文件已保存: {path}, size={size}")
    return stored_name, path, size


async def save_text(
    content: str,
    rec_id: int,
    category: str,
    ext: str = "txt",
    date_obj: datetime | None = None,
) -> str:
    """将文本内容保存到按日期组织的子目录中。

    文件保存到 storage_path/category/YYYY/MM/DD/{rec_id}_{category}.{ext}
    当 date_obj 为 None 时使用当前时间。
    返回文件完整路径。
    """
    subdir = get_dated_subdir(category, date_obj)
    filename = f"{rec_id}_{category}.{ext}"
    path = os.path.join(subdir, filename)
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(content)
    logger.info(f"文本已保存: {path}")
    return path


def delete_file_safe(path: str):
    """安全删除文件，忽略不存在或权限错误。"""
    if not path:
        return
    if os.path.exists(path):
        try:
            os.remove(path)
            logger.info(f"文件已删除: {path}")
        except OSError as e:
            logger.warning(f"删除文件失败: {path}, error={e}")


async def notify_webhook(webhook_url: str, rec_id: int, event: str):
    """发送 Webhook 回调通知。"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                webhook_url,
                json={
                    "event": event,
                    "recording_id": rec_id,
                },
            )
            logger.info(f"Webhook 通知已发送: url={webhook_url}, rec_id={rec_id}")
    except Exception as e:
        logger.warning(f"Webhook 通知失败: {e}")
