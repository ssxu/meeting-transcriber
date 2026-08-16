"""转录队列管理路由模块 - 管理待转录任务和定时转录设置。"""
import os
import logging
import asyncio
from datetime import datetime, timezone, time as dtime
from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db, async_session
from app.models import TranscriptionQueue, SystemSetting, Recording
from app.schemas import TranscriptionQueueOut, TranscriptionScheduleOut, TranscriptionScheduleUpdate
from app.routes.recordings import process_transcription

logger = logging.getLogger("meeting-transcriber.transcription")

router = APIRouter(prefix="/api/transcription-queue", tags=["transcription-queue"])

# 系统设置键名
SCHEDULE_ENABLED_KEY = "transcription_schedule_enabled"
SCHEDULE_START_KEY = "transcription_schedule_start"
SCHEDULE_END_KEY = "transcription_schedule_end"

# 定时轮询间隔（秒）
POLL_INTERVAL = 60
# 定时轮询任务句柄
_poll_task: asyncio.Task | None = None


async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    """获取系统设置值。"""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalars().first()
    return setting.value if setting else default


async def _set_setting(db: AsyncSession, key: str, value: str):
    """设置系统设置值。"""
    result = await db.execute(
        select(SystemSetting).where(SystemSetting.key == key)
    )
    setting = result.scalars().first()
    if setting:
        setting.value = value
    else:
        db.add(SystemSetting(key=key, value=value))


@router.get("")
async def list_queue(db: AsyncSession = Depends(get_db)):
    """获取待转录列表。"""
    result = await db.execute(
        select(TranscriptionQueue, Recording)
        .join(Recording, TranscriptionQueue.recording_id == Recording.id)
        .where(TranscriptionQueue.status.in_(["queued", "processing"]))
        .order_by(TranscriptionQueue.queued_at.asc())
    )
    rows = result.all()
    items = []
    for tq, rec in rows:
        items.append({
            "id": tq.id,
            "recording_id": tq.recording_id,
            "status": tq.status,
            "engine": tq.engine or "qwen_asr",
            "queued_at": tq.queued_at,
            "started_at": tq.started_at,
            "completed_at": tq.completed_at,
            "recording_title": rec.title or rec.original_filename,
            "recording_filename": rec.original_filename,
            "recording_status": rec.status,
            "recording_duration": rec.duration,
        })
    return items


def _parse_time(time_str: str) -> dtime:
    """将 HH:MM 字符串转为 datetime.time 对象，确保比较正确。"""
    try:
        parts = time_str.strip().split(":")
        return dtime(int(parts[0]), int(parts[1]))
    except Exception:
        return dtime(0, 0)


def _is_in_schedule_window(start_str: str, end_str: str) -> bool:
    """检查当前时间是否在允许的转录时间段内。使用 time 对象比较，避免字符串比较问题。"""
    start_t = _parse_time(start_str)
    end_t = _parse_time(end_str)
    now_t = datetime.now().time()
    if start_t <= end_t:
        # 正常区间，如 09:00 - 18:00
        return start_t <= now_t <= end_t
    else:
        # 跨天区间，如 22:00 - 06:00
        return now_t >= start_t or now_t <= end_t


async def add_recording_to_queue(
    recording_id: int,
    engine: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
):
    """公共函数：将录音加入转录队列并触发处理。
    
    被 upload_recording 和 add_to_queue 端点共用。
    """
    rec = await db.get(Recording, recording_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    
    # 检查是否已在队列中
    result = await db.execute(
        select(TranscriptionQueue).where(
            TranscriptionQueue.recording_id == recording_id,
            TranscriptionQueue.status.in_(["queued", "processing"]),
        )
    )
    existing = result.scalars().first()
    if existing:
        raise HTTPException(status_code=400, detail="该录音已在转录队列中")
    
    # 创建队列项
    tq = TranscriptionQueue(recording_id=recording_id, status="queued", engine=engine)
    db.add(tq)
    await db.commit()
    await db.refresh(tq)
    logger.info(f"录音 {recording_id} 已加入转录队列, queue_id={tq.id}, engine={engine}")
    
    # 检查是否需要立即处理
    schedule = await get_schedule(db)
    if not schedule["enabled"]:
        # 未启用时间段限制，立即处理
        background_tasks.add_task(process_queue_item, tq.id)
    else:
        # 启用了时间段限制，检查当前是否在时间段内
        if _is_in_schedule_window(schedule["start_time"], schedule["end_time"]):
            background_tasks.add_task(process_queue_item, tq.id)
        else:
            logger.info(f"当前不在转录时间段内 ({schedule['start_time']}-{schedule['end_time']}), 等待定时轮询处理")
    return tq


@router.post("/{recording_id}")
async def add_to_queue(
    recording_id: int,
    background_tasks: BackgroundTasks,
    engine: str = "qwen_asr",
    db: AsyncSession = Depends(get_db),
):
    """将录音添加到待转录队列。
    
    Args:
        engine: 转录引擎，qwen_asr (默认)
    """
    if engine not in ("qwen_asr",):
        raise HTTPException(status_code=400, detail=f"不支持的转录引擎: {engine}")
    tq = await add_recording_to_queue(recording_id, engine, background_tasks, db)
    return {"detail": "已加入转录队列", "queue_id": tq.id}


@router.delete("/{queue_id}")
async def cancel_queue_item(queue_id: int, db: AsyncSession = Depends(get_db)):
    """取消队列中的转录任务。"""
    tq = await db.get(TranscriptionQueue, queue_id)
    if not tq:
        raise HTTPException(status_code=404, detail="队列项不存在")
    if tq.status == "processing":
        raise HTTPException(status_code=400, detail="正在转录中，无法取消")
    if tq.status in ("done", "cancelled"):
        raise HTTPException(status_code=400, detail=f"任务已{tq.status}，无需取消")
    
    tq.status = "cancelled"
    await db.commit()
    await db.delete(tq)
    await db.commit()
    logger.info(f"队列项 {queue_id} 已取消并删除")
    return {"detail": "已取消"}


@router.delete("/{queue_id}/force")
async def force_cancel_queue_item(queue_id: int, db: AsyncSession = Depends(get_db)):
    """强制取消队列中的转录任务（即使正在处理中）。"""
    tq = await db.get(TranscriptionQueue, queue_id)
    if not tq:
        raise HTTPException(status_code=404, detail="队列项不存在")
    
    tq.status = "cancelled"
    await db.commit()
    await db.delete(tq)
    await db.commit()
    logger.info(f"队列项 {queue_id} 已强制取消并删除")
    return {"detail": "已强制取消"}


@router.patch("/{queue_id}/engine")
async def switch_queue_engine(
    queue_id: int,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """切换排队中任务的转录引擎。"""
    tq = await db.get(TranscriptionQueue, queue_id)
    if not tq:
        raise HTTPException(status_code=404, detail="队列项不存在")
    
    if tq.status != "queued":
        raise HTTPException(status_code=400, detail="只有排队中的任务可以切换引擎")
    
    new_engine = body.get("engine")
    if new_engine not in ("qwen_asr",):
        raise HTTPException(status_code=400, detail=f"不支持的转录引擎: {new_engine}")
    
    old_engine = tq.engine or "qwen_asr"
    tq.engine = new_engine
    await db.commit()
    logger.info(f"队列项 {queue_id} 引擎切换: {old_engine} -> {new_engine}")
    return {"detail": "引擎已切换", "engine": new_engine}


async def get_schedule(db: AsyncSession) -> dict:
    """获取转录时间设置。"""
    enabled = await _get_setting(db, SCHEDULE_ENABLED_KEY, "false")
    start = await _get_setting(db, SCHEDULE_START_KEY, "00:00")
    end = await _get_setting(db, SCHEDULE_END_KEY, "23:59")
    return {
        "enabled": enabled.lower() == "true",
        "start_time": start,
        "end_time": end,
    }


# ===== 转录时间设置 =====
schedule_router = APIRouter(prefix="/api/settings", tags=["transcription-schedule"])


@schedule_router.get("/transcription-schedule", response_model=TranscriptionScheduleOut)
async def get_transcription_schedule(db: AsyncSession = Depends(get_db)):
    """获取转录时间设置。"""
    sched = await get_schedule(db)
    return TranscriptionScheduleOut(**sched)


@schedule_router.put("/transcription-schedule", response_model=TranscriptionScheduleOut)
async def update_transcription_schedule(
    req: TranscriptionScheduleUpdate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """更新转录时间设置。"""
    await _set_setting(db, SCHEDULE_ENABLED_KEY, str(req.enabled).lower())
    await _set_setting(db, SCHEDULE_START_KEY, req.start_time)
    await _set_setting(db, SCHEDULE_END_KEY, req.end_time)
    await db.commit()
    logger.info(f"转录时间设置已更新: enabled={req.enabled}, {req.start_time}-{req.end_time}")
    
    # 如果启用且当前在时间段内，触发队列处理
    if req.enabled:
        if _is_in_schedule_window(req.start_time, req.end_time):
            background_tasks.add_task(process_pending_queue)
    else:
        # 未启用时间段限制，立即处理队列
        background_tasks.add_task(process_pending_queue)
    
    return TranscriptionScheduleOut(enabled=req.enabled, start_time=req.start_time, end_time=req.end_time)


async def process_queue_item(queue_id: int):
    """处理单个队列项：调用转录流程。"""
    async with async_session() as db:
        tq = await db.get(TranscriptionQueue, queue_id)
        if not tq or tq.status != "queued":
            return
        
        # 标记为处理中
        tq.status = "processing"
        tq.started_at = datetime.now(timezone.utc)
        await db.commit()
        
        recording_id = tq.recording_id
        engine = tq.engine or "qwen_asr"
        logger.info(f"开始处理队列项 {queue_id}, recording_id={recording_id}, engine={engine}")
    
    try:
        # 调用现有的转录流程，传入 engine
        await process_transcription(recording_id, engine=engine)
        
        # 标记为完成并删除
        async with async_session() as db:
            tq = await db.get(TranscriptionQueue, queue_id)
            if tq:
                await db.delete(tq)
                await db.commit()
                logger.info(f"队列项 {queue_id} 处理完成已删除")
        
        # 处理下一个队列项
        await process_pending_queue()
    except Exception as e:
        logger.error(f"队列项 {queue_id} 处理失败: {e}", exc_info=True)
        async with async_session() as db:
            tq = await db.get(TranscriptionQueue, queue_id)
            if tq:
                await db.delete(tq)
                await db.commit()
        logger.error(f"队列项 {queue_id} 处理失败已删除", exc_info=False)
        # 继续处理下一个
        await process_pending_queue()


async def process_pending_queue():
    """检查并处理队列中待处理的项（如果在允许的时间段内）。"""
    async with async_session() as db:
        # 检查时间段设置
        sched = await get_schedule(db)
        if sched["enabled"]:
            if not _is_in_schedule_window(sched["start_time"], sched["end_time"]):
                logger.debug(f"当前不在转录时间段内 ({sched['start_time']}-{sched['end_time']})")
                return
        # 查找是否有正在处理的项
        result = await db.execute(
            select(TranscriptionQueue).where(TranscriptionQueue.status == "processing")
        )
        processing = result.scalars().first()
        if processing:
            logger.debug(f"已有正在处理的队列项 {processing.id}，等待")
            return
        
        # 查找下一个待处理项
        result = await db.execute(
            select(TranscriptionQueue)
            .where(TranscriptionQueue.status == "queued")
            .order_by(TranscriptionQueue.queued_at.asc())
            .limit(1)
        )
        next_item = result.scalars().first()
        if next_item:
            logger.info(f"找到下一个待处理队列项: {next_item.id}")
            await process_queue_item(next_item.id)


async def _queue_poll_loop():
    """定时轮询任务：每 POLL_INTERVAL 秒检查一次队列。
    
    确保在转录时间段内时队列被处理，不需要用户手动触发。
    """
    logger.info(f"转录队列定时轮询已启动 (间隔 {POLL_INTERVAL}s)")
    while True:
        try:
            await process_pending_queue()
        except Exception as e:
            logger.error(f"队列轮询异常: {e}", exc_info=True)
        await asyncio.sleep(POLL_INTERVAL)


def start_queue_poller():
    """启动定时轮询后台任务（幂等，重复调用安全）。"""
    global _poll_task
    if _poll_task is not None and not _poll_task.done():
        return
    try:
        _poll_task = asyncio.create_task(_queue_poll_loop())
        logger.info("转录队列轮询任务已创建")
    except RuntimeError:
        # 没有事件循环（如同步上下文调用），跳过
        pass
