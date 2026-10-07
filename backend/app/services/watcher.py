"""目录监听服务模块 - 监听指定目录，自动处理新录音文件。"""
import os
import hashlib
import asyncio
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from threading import Thread
from typing import Set, Optional

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileSystemEvent

from app.config import settings
from app.utils.storage import get_dated_subdir

logger = logging.getLogger("meeting-transcriber.watcher")


class WatcherService:
    """目录监听服务类 - 单例模式。"""

    _instance: Optional["WatcherService"] = None

    def __init__(self):
        self.observer: Optional[Observer] = None
        self._processed_hashes: dict[str, datetime] = {}  # {file_hash: processed_time}
        self._processing_files: Set[str] = set()  # 正在处理的文件路径
        self._allowed_extensions: Set[str] = set()

    @classmethod
    def get_instance(cls) -> "WatcherService":
        if cls._instance is None:
            cls._instance = WatcherService()
        return cls._instance

    def _parse_extensions(self) -> Set[str]:
        """解析允许的文件扩展名。"""
        exts = set()
        for ext in settings.watch_allowed_extensions.split(","):
            ext = ext.strip().lower()
            if ext and not ext.startswith("."):
                ext = "." + ext
            exts.add(ext)
        return exts

    def _calculate_file_hash(self, file_path: str) -> str:
        """计算文件 MD5 指纹。"""
        hash_md5 = hashlib.md5()
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()

    def _is_duplicate(self, file_hash: str) -> bool:
        """检查是否为重复文件（防重复处理）。"""
        now = datetime.now()
        # 清理过期记录
        expired = [h for h, t in self._processed_hashes.items()
                   if (now - t).total_seconds() > settings.watch_dedup_interval]
        for h in expired:
            del self._processed_hashes[h]

        if file_hash in self._processed_hashes:
            return True
        self._processed_hashes[file_hash] = now
        return False

    def _is_allowed_extension(self, file_path: str) -> bool:
        """检查文件扩展名是否允许。"""
        if not self._allowed_extensions:
            self._allowed_extensions = self._parse_extensions()
        ext = os.path.splitext(file_path)[1].lower()
        return ext in self._allowed_extensions

    async def _process_file(self, file_path: str):
        """异步处理单个录音文件。"""
        if file_path in self._processing_files:
            logger.info(f"文件正在处理中，跳过: {file_path}")
            return

        self._processing_files.add(file_path)
        try:
            # 等待文件就绪（避免文件写入不完整）
            await asyncio.sleep(settings.watch_file_ready_delay)

            # 验证文件存在且可读
            if not os.path.exists(file_path) or not os.path.isfile(file_path):
                logger.warning(f"文件不存在或不是文件: {file_path}")
                return

            # 计算文件指纹
            try:
                file_hash = self._calculate_file_hash(file_path)
            except Exception as e:
                logger.error(f"计算文件指纹失败: {file_path}, error={e}")
                return

            # 防重复检查
            if self._is_duplicate(file_hash):
                logger.info(f"文件已处理过，跳过: {file_path}")
                return

            logger.info(f"开始处理录音文件: {file_path}")

            # 调用处理流程
            await self._process_recording_file(file_path)

            # 处理成功后删除源文件
            if settings.watch_delete_on_success:
                try:
                    os.remove(file_path)
                    logger.info(f"源文件已删除: {file_path}")
                except OSError as e:
                    logger.warning(f"删除源文件失败: {file_path}, error={e}")

        except Exception as e:
            logger.error(f"处理录音文件失败: {file_path}, error={e}", exc_info=True)
        finally:
            self._processing_files.discard(file_path)

    async def _process_recording_file(self, file_path: str):
        """处理录音文件 - 复用现有上传流程的核心逻辑。"""
        from app.db import async_session
        from app.models import Recording

        filename = os.path.basename(file_path)
        file_size = os.path.getsize(file_path)
        ext = os.path.splitext(filename)[1].lower()

        # 确定 content_type
        content_type_map = {
            ".mp3": "audio/mpeg",
            ".wav": "audio/wav",
            ".m4a": "audio/mp4",
            ".flac": "audio/flac",
            ".ogg": "audio/ogg",
            ".aac": "audio/aac",
            ".opus": "audio/opus",
            ".webm": "audio/webm",
            ".mp4": "video/mp4",
            ".mkv": "video/x-matroska",
            ".avi": "video/x-msvideo",
            ".mov": "video/quicktime",
            ".wmv": "video/x-ms-wmv",
        }
        content_type = content_type_map.get(ext, "application/octet-stream")

        # 生成存储文件名和路径
        stored_name = f"{uuid.uuid4().hex}{ext}"
        audio_path = get_dated_subdir("recordings") / stored_name

        # 复制文件到存储目录
        shutil.copy2(file_path, audio_path)

        # 创建 Recording 记录
        async with async_session() as db:
            rec = Recording(
                title=filename,
                original_filename=filename,
                stored_filename=stored_name,
                audio_path=str(audio_path),
                content_type=content_type,
                file_size=file_size,
                status="pending",
            )
            db.add(rec)
            await db.commit()
            await db.refresh(rec)
            rec_id = rec.id

        logger.info(f"录音记录已创建: id={rec_id}, filename={filename}")

        # 触发转录流程（后台任务）
        await self._run_transcription_workflow(rec_id)

    async def _run_transcription_workflow(self, rec_id: int):
        """执行转录工作流。"""
        # 延迟导入避免循环引用
        from app.routes.recordings import process_transcription
        from app.db import async_session
        from app.models import Recording

        async with async_session() as db:
            rec = await db.get(Recording, rec_id)
            if not rec:
                logger.error(f"录音记录不存在: id={rec_id}")
                return

            rec.status = "transcribing"
            await db.commit()

        # 调用转录
        await process_transcription(rec_id, "qwen_asr")

        logger.info(f"录音处理完成: rec_id={rec_id}")

    def _on_file_created(self, file_path: str):
        """文件创建事件处理。"""
        if not self._is_allowed_extension(file_path):
            return

        # 使用 asyncio 在后台异步处理
        asyncio.create_task(self._process_file(file_path))

    def start(self):
        """启动监听服务。"""
        if not settings.watch_enabled:
            logger.info("目录监听功能未启用")
            return

        if self.observer is not None:
            logger.warning("监听服务已在运行")
            return

        self._allowed_extensions = self._parse_extensions()

        watch_path = settings.watch_directory
        if not os.path.exists(watch_path):
            try:
                os.makedirs(watch_path, exist_ok=True)
                logger.info(f"监听目录已创建: {watch_path}")
            except OSError as e:
                logger.error(f"无法创建监听目录: {watch_path}, error={e}")
                return

        # 创建事件处理器
        handler = RecordingEventHandler(self)

        # 创建并启动观察者
        self.observer = Observer()
        self.observer.schedule(
            handler,
            watch_path,
            recursive=settings.watch_recursive
        )
        self.observer.start()

        logger.info(f"目录监听服务已启动: path={watch_path}, recursive={settings.watch_recursive}")

    def stop(self):
        """停止监听服务。"""
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=5)
            self.observer = None
            logger.info("目录监听服务已停止")


class RecordingEventHandler(FileSystemEventHandler):
    """录音文件事件处理器。"""

    def __init__(self, watcher: WatcherService):
        super().__init__()
        self.watcher = watcher

    def on_created(self, event: FileSystemEvent):
        """文件创建事件。"""
        if event.is_directory:
            return
        self.watcher._on_file_created(event.src_path)


# ===== 全局实例和生命周期管理 =====

_watcher_instance: Optional[WatcherService] = None


def get_watcher() -> WatcherService:
    """获取监听服务单例。"""
    global _watcher_instance
    if _watcher_instance is None:
        _watcher_instance = WatcherService()
    return _watcher_instance


async def start_watcher():
    """异步启动监听服务（在应用启动时调用）。"""
    watcher = get_watcher()
    # 在新的线程中启动 observer（watchdog 需要线程）
    thread = Thread(target=watcher.start, daemon=True)
    thread.start()


def stop_watcher():
    """停止监听服务（在应用关闭时调用）。"""
    watcher = get_watcher()
    watcher.stop()
