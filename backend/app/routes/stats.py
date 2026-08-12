"""统计信息路由模块 - 仪表盘数据接口与会议效率分析。"""
import json
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import Recording
from app.utils import _get_segments
from app.schemas import (
    StatsOut,
    EnhancedStatsOut,
    MeetingAnalysisOut,
    SpeakerStat,
    SpeakerSummaryOut,
)

logger = logging.getLogger("meeting-transcriber.stats")

router = APIRouter(prefix="/api/stats", tags=["stats"])


def _parse_date(date_str: str) -> datetime | None:
    """解析 ISO 日期字符串为 UTC datetime。"""
    if not date_str or not date_str.strip():
        return None
    try:
        # 支持 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM:SS
        dt = datetime.fromisoformat(date_str.strip())
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None





@router.get("", response_model=EnhancedStatsOut)
async def get_stats(
    date_from: str = Query("", description="起始日期 ISO 格式"),
    date_to: str = Query("", description="结束日期 ISO 格式"),
    db: AsyncSession = Depends(get_db),
):
    """获取仪表盘统计数据：录音总数、总时长、状态分布、近7天趋势、说话人数、平均时长。
    支持按日期范围过滤。
    """
    # 解析日期范围
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)

    # 基础查询
    query = select(Recording)
    if dt_from:
        query = query.where(Recording.created_at >= dt_from)
    if dt_to:
        query = query.where(Recording.created_at <= dt_to)

    # 总数和总时长
    count_duration_query = select(
        func.count(Recording.id),
        func.coalesce(func.sum(Recording.duration), 0),
    )
    if dt_from:
        count_duration_query = count_duration_query.where(Recording.created_at >= dt_from)
    if dt_to:
        count_duration_query = count_duration_query.where(Recording.created_at <= dt_to)
    result = await db.execute(count_duration_query)
    total, total_duration = result.one()

    # 状态分布
    status_query = select(
        Recording.status,
        func.count(Recording.id),
    ).group_by(Recording.status)
    if dt_from:
        status_query = status_query.where(Recording.created_at >= dt_from)
    if dt_to:
        status_query = status_query.where(Recording.created_at <= dt_to)
    result = await db.execute(status_query)
    status_breakdown = {row[0]: row[1] for row in result.all()}

    # 近7天数据（不受日期过滤影响，始终反映最近7天）
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)
    result = await db.execute(
        select(
            func.count(Recording.id),
            func.coalesce(func.sum(Recording.duration), 0),
        ).where(Recording.created_at >= seven_days_ago)
    )
    recent_7d_count, recent_7d_duration = result.one()

    # 说话人数量（跨所有录音去重）
    all_recs_result = await db.execute(
        select(Recording.transcript_segments).where(Recording.transcript_segments.isnot(None))
    )
    speaker_set = set()
    for row in all_recs_result.scalars():
        segs = row
        if isinstance(segs, str):
            try:
                segs = json.loads(segs)
            except json.JSONDecodeError:
                continue
        if isinstance(segs, list):
            for seg in segs:
                if isinstance(seg, dict):
                    sp = seg.get("speaker")
                    if sp and isinstance(sp, str):
                        speaker_set.add(sp)
    speaker_count = len(speaker_set)

    # 平均时长
    avg_duration = round(float(total_duration) / total, 1) if total > 0 else 0.0

    # 标签统计
    tag_query = select(Recording.tags).where(Recording.tags.isnot(None))
    if dt_from:
        tag_query = tag_query.where(Recording.created_at >= dt_from)
    if dt_to:
        tag_query = tag_query.where(Recording.created_at <= dt_to)
    tag_result = await db.execute(tag_query)
    tag_count: dict[str, int] = {}
    for row in tag_result.scalars():
        if isinstance(row, list):
            for t in row:
                if t and isinstance(t, str):
                    tag_count[t] = tag_count.get(t, 0) + 1
    tag_stats = sorted(
        [{"tag": k, "count": v} for k, v in tag_count.items()],
        key=lambda x: x["count"],
        reverse=True,
    )[:20]  # 最多返回 20 个标签

    # 日期范围信息
    date_range = None
    if dt_from or dt_to:
        date_range = {
            "date_from": dt_from.isoformat() if dt_from else None,
            "date_to": dt_to.isoformat() if dt_to else None,
        }

    return EnhancedStatsOut(
        total=total,
        total_duration=round(float(total_duration), 1),
        status_breakdown=status_breakdown,
        recent_7d_count=recent_7d_count,
        recent_7d_duration=round(float(recent_7d_duration), 1),
        date_range=date_range,
        speaker_count=speaker_count,
        avg_duration=avg_duration,
        tag_stats=tag_stats,
    )


@router.get("/meeting-analysis/{rec_id}", response_model=MeetingAnalysisOut)
async def get_meeting_analysis(rec_id: int, db: AsyncSession = Depends(get_db)):
    """会议效率分析：说话人时长分布、参与度等。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")

    segs = _get_segments(rec)
    total_duration = rec.duration or 0.0

    # 按说话人聚合
    speaker_data: dict[str, dict] = {}  # speaker -> {total_time, count}
    for seg in segs:
        if not isinstance(seg, dict):
            continue
        speaker = seg.get("speaker") or "未知说话人"
        start = seg.get("start", 0)
        end = seg.get("end", start)
        seg_duration = max(0, end - start)
        if speaker not in speaker_data:
            speaker_data[speaker] = {"total_time": 0.0, "count": 0}
        speaker_data[speaker]["total_time"] += seg_duration
        speaker_data[speaker]["count"] += 1

    speaker_stats: list[SpeakerStat] = []
    for speaker, data in sorted(speaker_data.items(), key=lambda x: x[1]["total_time"], reverse=True):
        total_time = data["total_time"]
        count = data["count"]
        avg_len = round(total_time / count, 1) if count > 0 else 0.0
        pct = round(total_time / total_duration * 100, 1) if total_duration > 0 else 0.0
        speaker_stats.append(SpeakerStat(
            speaker=speaker,
            total_speak_time=round(total_time, 1),
            speak_count=count,
            avg_segment_length=avg_len,
            percentage=pct,
        ))

    # 参与率：发言时长超过总时长 10% 的说话人占比
    active_speakers = sum(1 for s in speaker_stats if s.percentage > 10.0)
    participation_rate = round(active_speakers / len(speaker_stats) * 100, 1) if speaker_stats else 0.0

    return MeetingAnalysisOut(
        rec_id=rec.id,
        title=rec.title,
        total_duration=round(total_duration, 1),
        speaker_count=len(speaker_stats),
        speaker_stats=speaker_stats,
        participation_rate=participation_rate,
    )


@router.get("/speaker-summary", response_model=list[SpeakerSummaryOut])
async def get_speaker_summary(
    date_from: str = Query("", description="起始日期 ISO 格式"),
    date_to: str = Query("", description="结束日期 ISO 格式"),
    db: AsyncSession = Depends(get_db),
):
    """说话人维度统计：跨所有会议的说话人汇总数据。支持按日期范围过滤。"""
    dt_from = _parse_date(date_from)
    dt_to = _parse_date(date_to)

    query = select(Recording).where(
        Recording.transcript_segments.isnot(None),
        Recording.status == "done",
    )
    if dt_from:
        query = query.where(Recording.created_at >= dt_from)
    if dt_to:
        query = query.where(Recording.created_at <= dt_to)

    result = await db.execute(query.order_by(Recording.created_at.desc()))
    recs = result.scalars().all()

    # 按说话人聚合跨会议数据
    speaker_meetings: dict[str, set] = {}  # speaker -> set of rec_ids
    speaker_speak_time: dict[str, float] = {}
    speaker_segments: dict[str, int] = {}

    for rec in recs:
        segs = _get_segments(rec)
        rec_speakers = set()
        for seg in segs:
            if not isinstance(seg, dict):
                continue
            speaker = seg.get("speaker") or "未知说话人"
            start = seg.get("start", 0)
            end = seg.get("end", start)
            seg_duration = max(0, end - start)
            rec_speakers.add(speaker)
            speaker_speak_time[speaker] = speaker_speak_time.get(speaker, 0.0) + seg_duration
            speaker_segments[speaker] = speaker_segments.get(speaker, 0) + 1
        for sp in rec_speakers:
            if sp not in speaker_meetings:
                speaker_meetings[sp] = set()
            speaker_meetings[sp].add(rec.id)

    out = []
    for speaker in sorted(speaker_speak_time.keys(), key=lambda s: speaker_speak_time[s], reverse=True):
        meeting_count = len(speaker_meetings.get(speaker, set()))
        total_time = speaker_speak_time[speaker]
        total_segs = speaker_segments.get(speaker, 0)
        avg_per_meeting = round(total_time / meeting_count, 1) if meeting_count > 0 else 0.0
        out.append(SpeakerSummaryOut(
            speaker=speaker,
            total_meetings=meeting_count,
            total_speak_time=round(total_time, 1),
            total_segments=total_segs,
            avg_speak_time_per_meeting=avg_per_meeting,
        ))

    return out
