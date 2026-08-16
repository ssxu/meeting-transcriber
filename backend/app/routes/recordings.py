"""录音管理路由模块 - 上传/列表/详情/删除/导出/重命名/批量操作等。"""
import os
import io
import re
import json
import time
import hashlib
import logging
from datetime import datetime, timezone
from urllib.parse import quote
from fastapi import APIRouter, UploadFile, File, BackgroundTasks, HTTPException, Depends, Request, Form, Query
from fastapi.responses import FileResponse, PlainTextResponse, StreamingResponse, Response
from sqlalchemy import select, or_, delete, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db, async_session
from app.models import Recording, HotwordLibrary, Hotword, MeetingType
from app.schemas import (
    RecordingOut, RecordingDetail, SearchResult,
    BatchDeleteRequest, RenameRequest,
    RegenerateRequest, RegenerateActionItemsRequest,
    RegenerateKeywordsRequest, RegenerateMindmapRequest,
    TranscriptEditRequest, SegmentMergeRequest, SegmentSplitRequest,
    ChatRequest, ChatResponse,
)
from app.services import asr, llm
from app.services.llm import ModelConfig, get_default_model_config
from app.utils.timezone import format_datetime
from app.utils import storage
from app.utils import _get_segments
from app.config import settings
from app.routes.models import get_model_config_by_id
from urllib.parse import quote
import zipfile

logger = logging.getLogger("meeting-transcriber.recordings")

router = APIRouter(prefix="/api/recordings", tags=["recordings"])

ALLOWED_AUDIO = {
    ".wav", ".mp3", ".m4a", ".flac", ".ogg", ".wma",
    ".aac", ".opus", ".webm", ".mp4", ".mkv", ".avi",
    ".mov", ".wmv",
}


@router.post("/upload", response_model=RecordingOut)
async def upload_recording(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    srt_file: UploadFile | None = File(None),
    engine: str = Form("qwen_asr"),
    hotword_library_id: int | None = Form(None),
    meeting_type_id: int | None = Form(None),
    asr_provider_id: int | None = Form(None),
    db: AsyncSession = Depends(get_db),
):
    """上传录音文件，自动触发转录和摘要。

    可选参数:
      - srt_file: 字幕/转录文件（选传，支持 SRT 和 TXT 格式）。如果上传了此文件，
        则跳过 ASR 转录，直接使用文件内容作为逐字稿，并自动触发后续的摘要生成等流程。
      - engine: 转录引擎，qwen_asr（默认，支持说话人识别）
      - hotword_library_id: 热词库ID，传递给ASR转录接口的vocabulary_id
      - meeting_type_id: 会议类型ID，对应的summary_prompt传递给LLM总结接口
      - asr_provider_id: ASR 提供商 ID（选传，指定后固定使用该提供商）
    """
    # 验证 engine 参数
    if engine not in ("qwen_asr",):
        raise HTTPException(status_code=400, detail=f"不支持的转录引擎: {engine}")

    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_AUDIO:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的文件格式: {ext}，支持: {', '.join(sorted(ALLOWED_AUDIO))}"
        )

    stored_name, audio_path, size = await storage.save_upload(file)
    rec = Recording(
        title=file.filename or "recording",
        original_filename=file.filename or "recording",
        stored_filename=stored_name,
        audio_path=audio_path,
        content_type=file.content_type or "audio/mpeg",
        file_size=size,
        status="pending",
        hotword_library_id=hotword_library_id,
        meeting_type_id=meeting_type_id,
        asr_provider_id=asr_provider_id,
    )
    db.add(rec)
    await db.commit()
    await db.refresh(rec)
    logger.info(
        f"录音已上传: id={rec.id}, filename={rec.original_filename}, size={size}, "
        f"hotword_lib={hotword_library_id}, meeting_type={meeting_type_id}, "
        f"engine={engine}, asr_provider_id={asr_provider_id}, srt_file={'yes' if srt_file else 'no'}"
    )

    # 如果上传了 SRT 文件，跳过 ASR 转录，直接解析 SRT 并触发生成摘要
    if srt_file:
        try:
            content_bytes = await srt_file.read()
            # 尝试 UTF-8，回退 GBK
            try:
                srt_content = content_bytes.decode('utf-8-sig')  # utf-8-sig 自动去除 BOM
            except UnicodeDecodeError:
                try:
                    srt_content = content_bytes.decode('gbk')
                except UnicodeDecodeError:
                    srt_content = content_bytes.decode('utf-8', errors='replace')
            
            # 根据文件扩展名自动选择解析器（支持 SRT 和 TXT）
            transcript_text, segments = parse_transcript_file(srt_content, srt_file.filename or "")
            if not transcript_text:
                logger.warning(f"字幕/转录文件解析失败，回退到 ASR 转录: id={rec.id}")
                # 解析失败，走队列转录
                from app.routes.transcription import add_recording_to_queue
                await add_recording_to_queue(rec.id, engine, background_tasks, db)
                return RecordingOut.model_validate(rec)
            
            async with async_session() as srt_db:
                srt_rec = await srt_db.get(Recording, rec.id)
                if srt_rec:
                    srt_rec.transcript_text = transcript_text
                    srt_rec.transcript_segments = segments
                    srt_rec.status = "transcribed"
                    srt_rec.transcript_path = await storage.save_text(
                        transcript_text, rec.id, "transcripts", "txt"
                    )
                    # 尝试从音频文件获取时长
                    if srt_rec.audio_path and os.path.exists(srt_rec.audio_path) and not srt_rec.duration:
                        try:
                            import mutagen
                            audio = mutagen.File(srt_rec.audio_path)
                            if audio and audio.info:
                                srt_rec.duration = audio.info.length
                        except Exception:
                            pass
                    # 从 SRT 字幕时长补充录音时长
                    if segments and (not srt_rec.duration or srt_rec.duration == 0):
                        srt_rec.duration = segments[-1]["end"]
                    await srt_db.commit()
            logger.info(f"字幕/转录文件上传解析成功，跳过 ASR 转录: id={rec.id}, segments={len(segments)}")
            # 触发后续摘要生成流程
            background_tasks.add_task(run_summary, rec.id, await _get_default_model_config_safe())
        except Exception as e:
            logger.error(f"字幕/转录文件处理失败，回退到 ASR 转录: id={rec.id}, error={e}", exc_info=True)
            # SRT 处理失败，走队列转录
            from app.routes.transcription import add_recording_to_queue
            await add_recording_to_queue(rec.id, engine, background_tasks, db)
    else:
        # 无 SRT 文件，走转录队列（尊重时间段设置和引擎选择）
        from app.routes.transcription import add_recording_to_queue
        await add_recording_to_queue(rec.id, engine, background_tasks, db)
    return RecordingOut.model_validate(rec)


@router.get("")
async def list_recordings(
    page: int = 1,
    page_size: int = 12,
    q: str = "",
    tag: str = "",
    db: AsyncSession = Depends(get_db),
):
    """获取录音列表，支持分页、关键词搜索和标签过滤。
    
    参数:
      - page: 页码，从1开始
      - page_size: 每页条数，默认12
      - q: 搜索关键词（匹配标题、文件名、逐字稿、摘要）
      - tag: 标签过滤（精确匹配）
    """
    page = max(1, page)
    page_size = max(1, min(100, page_size))
    
    query = select(Recording)
    
    # 关键词搜索
    if q.strip():
        # 转义 LIKE 通配符，防止用户输入 % 或 _ 匹配意外结果
        escaped_q = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f"%{escaped_q}%"
        query = query.where(
            or_(
                Recording.transcript_text.like(pattern, escape='\\'),
                Recording.summary_text.like(pattern, escape='\\'),
                Recording.original_filename.like(pattern, escape='\\'),
                Recording.title.like(pattern, escape='\\'),
            )
        )
    
    # 标签过滤
    if tag.strip():
        # SQLite JSON 查询：tags 列存储为 JSON 数组，使用 LIKE 模糊匹配
        escaped_tag = tag.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        query = query.where(Recording.tags.like(f'%"{escaped_tag}"%', escape='\\'))
    
    # 总数
    count_query = select(func.count()).select_from(query.subquery())
    total_result = await db.execute(count_query)
    total = total_result.scalar() or 0
    
    # 分页
    query = query.order_by(Recording.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    result = await db.execute(query)
    items = [RecordingOut.model_validate(r) for r in result.scalars().all()]
    
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
    }


@router.get("/tags/list")
async def list_all_tags(db: AsyncSession = Depends(get_db)):
    """获取所有录音中使用过的标签列表（去重）。"""
    result = await db.execute(
        select(Recording.tags).where(Recording.tags.isnot(None))
    )
    tag_set = set()
    for row in result.scalars():
        if isinstance(row, list):
            for t in row:
                if t and isinstance(t, str):
                    tag_set.add(t)
    return sorted(tag_set)


@router.get("/search", response_model=list[SearchResult])
async def search_recordings(q: str, db: AsyncSession = Depends(get_db)):
    """全文搜索录音的逐字稿、摘要和文件名。"""
    if not q.strip():
        return []
    escaped_q = q.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
    pattern = f"%{escaped_q}%"
    result = await db.execute(
        select(Recording)
        .where(
            or_(
                Recording.transcript_text.like(pattern, escape='\\'),
                Recording.summary_text.like(pattern, escape='\\'),
                Recording.original_filename.like(pattern, escape='\\'),
                Recording.title.like(pattern, escape='\\'),
            )
        )
        .order_by(Recording.created_at.desc())
    )
    rows = result.scalars().all()
    out = []
    for r in rows:
        src = r.transcript_text or r.summary_text or ""
        idx = src.lower().find(q.lower())
        if idx >= 0:
            start = max(0, idx - 40)
            end = min(len(src), idx + len(q) + 60)
            snippet = ("..." if start > 0 else "") + src[start:end] + ("..." if end < len(src) else "")
        else:
            snippet = src[:100] + "..." if len(src) > 100 else src
        out.append(SearchResult(
            id=r.id,
            title=r.title,
            original_filename=r.original_filename,
            status=r.status,
            snippet=snippet,
            created_at=r.created_at,
        ))
    return out


# ---------------- 导出接口 ----------------

def _fmt_ts(seconds: float) -> str:
    """格式化秒为 HH:MM:SS。"""
    if not seconds or seconds < 0:
        return "00:00:00"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h:02d}:{m:02d}:{s:02d}"


def _fmt_srt_ts(seconds: float) -> str:
    """格式化秒为 SRT 时间戳 HH:MM:SS,mmm。"""
    if not seconds or seconds < 0:
        return "00:00:00,000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_ts(seconds: float) -> str:
    """格式化秒为 VTT 时间戳 HH:MM:SS.mmm。"""
    if not seconds or seconds < 0:
        return "00:00:00.000"
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds - int(seconds)) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


@router.get("/export/{rec_id}/txt", response_class=PlainTextResponse)
async def export_txt(
    rec_id: int,
    with_timestamps: bool = Query(False),
    with_speakers: bool = Query(False),
    db: AsyncSession = Depends(get_db),
):
    """导出逐字稿为 TXT 文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    segs = _get_segments(rec)
    if not segs:
        # 无 segment 数据，直接返回纯文本
        content = rec.transcript_text or "（暂无逐字稿）"
    else:
        lines = []
        for seg in segs:
            text = seg.get("text", "").strip()
            if not text:
                continue
            prefix_parts = []
            if with_timestamps:
                start = seg.get("start", 0)
                prefix_parts.append(f"[{_fmt_ts(start)}]")
            if with_speakers:
                speaker = seg.get("speaker", "")
                prefix_parts.append(f"[{speaker}]")
            if prefix_parts:
                lines.append(f"{' '.join(prefix_parts)} {text}")
            else:
                lines.append(text)
        content = "\n".join(lines)
    safe_name = (rec.title or rec.original_filename or "transcript").replace(" ", "_").replace("/", "_")
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.txt"'},
        media_type="text/plain; charset=utf-8",
    )


@router.get("/export/{rec_id}/srt", response_class=PlainTextResponse)
async def export_srt(rec_id: int, db: AsyncSession = Depends(get_db)):
    """导出为 SRT 字幕文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    segs = _get_segments(rec)
    if not segs:
        # 无 segment 数据，用纯文本生成单条 SRT
        text = rec.transcript_text or "（暂无逐字稿）"
        content = f"1\n00:00:00,000 --> 00:00:10,000\n{text}\n"
    else:
        blocks = []
        for i, seg in enumerate(segs, 1):
            text = seg.get("text", "").strip()
            if not text:
                continue
            start = seg.get("start", 0)
            end = seg.get("end", start + 1)
            blocks.append(f"{i}\n{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}\n{text}\n")
        content = "\n".join(blocks)
    safe_name = (rec.title or rec.original_filename or "transcript").replace(" ", "_").replace("/", "_")
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.srt"'},
        media_type="text/plain; charset=utf-8",
    )


@router.get("/export/{rec_id}/vtt", response_class=PlainTextResponse)
async def export_vtt(rec_id: int, db: AsyncSession = Depends(get_db)):
    """导出为 WebVTT 字幕文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    segs = _get_segments(rec)
    parts = ["WEBVTT", ""]
    if not segs:
        text = rec.transcript_text or "（暂无逐字稿）"
        parts.append(f"00:00:00.000 --> 00:00:10.000\n{text}\n")
    else:
        for seg in segs:
            text = seg.get("text", "").strip()
            if not text:
                continue
            start = seg.get("start", 0)
            end = seg.get("end", start + 1)
            parts.append(f"{_fmt_vtt_ts(start)} --> {_fmt_vtt_ts(end)}\n{text}\n")
    content = "\n".join(parts)
    safe_name = (rec.title or rec.original_filename or "transcript").replace(" ", "_").replace("/", "_")
    return PlainTextResponse(
        content=content,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.vtt"'},
        media_type="text/vtt; charset=utf-8",
    )


@router.get("/export/{rec_id}/pdf")
async def export_pdf(rec_id: int, db: AsyncSession = Depends(get_db)):
    """导出为 PDF 文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")

    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.platypus import (
        SimpleDocTemplate, Paragraph, Spacer, PageBreak,
    )
    from reportlab.lib.enums import TA_CENTER
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib import colors

    # 注册中文字体（尝试常见路径）
    font_name = "Helvetica"
    cn_font_paths = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for font_path in cn_font_paths:
        if os.path.exists(font_path):
            try:
                pdfmetrics.registerFont(TTFont("CNFont", font_path))
                font_name = "CNFont"
                break
            except Exception:
                pass
    else:
        # 所有路径都找不到，尝试使用 reportlab 内置 CID 中文字体
        try:
            from reportlab.pdfbase.cidfonts import UnicodeCIDFont
            pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
            font_name = "STSong-Light"
        except Exception:
            logger.warning("未找到中文字体，PDF 中文可能无法正常显示")

    buf = io.BytesIO()
    page_w, page_h = A4

    def _on_page(canvas, doc):
        canvas.saveState()
        # 页眉
        canvas.setFont(font_name, 9)
        canvas.setFillColor(colors.grey)
        header = f"{rec.title or rec.original_filename or '会议记录'}"
        if rec.created_at:
            header += f"  |  {format_datetime(rec.created_at, '%Y-%m-%d')}"
        canvas.drawString(20 * mm, page_h - 15 * mm, header)
        canvas.line(20 * mm, page_h - 17 * mm, page_w - 20 * mm, page_h - 17 * mm)
        # 页脚
        canvas.drawCentredString(page_w / 2, 10 * mm, f"第 {doc.page} 页")
        canvas.restoreState()

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=25 * mm, bottomMargin=20 * mm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("CnTitle", parent=styles["Title"], fontName=font_name, fontSize=20, alignment=TA_CENTER)
    h2_style = ParagraphStyle("CnH2", parent=styles["Heading2"], fontName=font_name, fontSize=14)
    body_style = ParagraphStyle("CnBody", parent=styles["Normal"], fontName=font_name, fontSize=10.5, leading=18)
    meta_style = ParagraphStyle("CnMeta", parent=styles["Normal"], fontName=font_name, fontSize=9, textColor=colors.grey)

    story = []
    story.append(Paragraph(rec.title or "会议记录", title_style))
    story.append(Spacer(1, 6 * mm))
    # 元信息
    meta_parts = []
    if rec.created_at:
        meta_parts.append(f"日期: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}")
    if rec.duration:
        meta_parts.append(f"时长: {format_duration(rec.duration)}")
    if rec.language:
        meta_parts.append(f"语言: {rec.language}")
    if meta_parts:
        story.append(Paragraph("  |  ".join(meta_parts), meta_style))
    story.append(Spacer(1, 8 * mm))

    # 摘要
    if rec.summary_text:
        story.append(Paragraph("会议纪要", h2_style))
        story.append(Spacer(1, 3 * mm))
        for line in rec.summary_text.split("\n"):
            line = line.strip()
            if line:
                story.append(Paragraph(line, body_style))
        story.append(Spacer(1, 6 * mm))

    # 待办事项
    if rec.action_items:
        story.append(Paragraph("待办事项", h2_style))
        story.append(Spacer(1, 3 * mm))
        for item in rec.action_items:
            story.append(Paragraph(f"• {item}", body_style))
        story.append(Spacer(1, 6 * mm))

    # 逐字稿
    story.append(Paragraph("逐字稿", h2_style))
    story.append(Spacer(1, 3 * mm))
    segs = _get_segments(rec)
    if segs:
        for seg in segs:
            text = seg.get("text", "").strip()
            if not text:
                continue
            speaker = seg.get("speaker", "")
            start = seg.get("start", 0)
            ts = _fmt_ts(start)
            if speaker:
                label = f"[{ts}] [{speaker}] {text}"
            else:
                label = f"[{ts}] {text}"
            story.append(Paragraph(label, body_style))
    else:
        raw = rec.transcript_text or "（暂无逐字稿）"
        for line in raw.split("\n"):
            if line.strip():
                story.append(Paragraph(line.strip(), body_style))

    doc.build(story, onFirstPage=_on_page, onLaterPages=_on_page)
    buf.seek(0)
    safe_name = (rec.title or rec.original_filename or "transcript").replace(" ", "_").replace("/", "_")
    return StreamingResponse(
        buf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.pdf"'},
    )


@router.get("/export/{rec_id}/docx")
async def export_docx(rec_id: int, db: AsyncSession = Depends(get_db)):
    """导出为 Word 文档。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")

    from docx import Document
    from docx.shared import Pt, Inches, RGBColor
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    doc = Document()

    # 标题
    title_para = doc.add_heading(rec.title or "会议记录", level=0)
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # 元信息
    meta_lines = []
    if rec.created_at:
        meta_lines.append(f"日期: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}")
    if rec.duration:
        meta_lines.append(f"时长: {format_duration(rec.duration)}")
    if rec.language:
        meta_lines.append(f"语言: {rec.language}")
    if meta_lines:
        meta_para = doc.add_paragraph("  |  ".join(meta_lines))
        for run in meta_para.runs:
            run.font.size = Pt(9)
            run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    doc.add_paragraph("")  # spacer

    # 摘要
    if rec.summary_text:
        doc.add_heading("会议纪要", level=1)
        for line in rec.summary_text.split("\n"):
            line = line.strip()
            if line:
                doc.add_paragraph(line)

    # 待办事项
    if rec.action_items:
        doc.add_heading("待办事项", level=1)
        for item in rec.action_items:
            doc.add_paragraph(item, style="List Bullet")

    # 逐字稿
    doc.add_heading("逐字稿", level=1)
    segs = _get_segments(rec)
    if segs:
        for seg in segs:
            text = seg.get("text", "").strip()
            if not text:
                continue
            speaker = seg.get("speaker", "")
            start = seg.get("start", 0)
            ts = _fmt_ts(start)
            if speaker:
                label = f"[{ts}] [{speaker}] "
            else:
                label = f"[{ts}] "
            p = doc.add_paragraph()
            run = p.add_run(label)
            run.bold = True
            run.font.size = Pt(9)
            p.add_run(text)
    else:
        raw = rec.transcript_text or "（暂无逐字稿）"
        for line in raw.split("\n"):
            if line.strip():
                doc.add_paragraph(line.strip())

    buf = io.BytesIO()
    doc.save(buf)
    buf.seek(0)
    safe_name = (rec.title or rec.original_filename or "transcript").replace(" ", "_").replace("/", "_")
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.docx"'},
    )


@router.get("/batch-export")
async def batch_export(
    ids: str,
    db: AsyncSession = Depends(get_db),
):
    """批量导出多个录音为 ZIP 文件（包含多个 Markdown 文件）。
    
    修复: 原来合并为单个 md 文件且 Content-Disposition filename 含中文导致 latin-1 编码错误。
    现在改为 ZIP 打包，filename 使用 URL 编码。
    """
    try:
        id_list = [int(x) for x in ids.split(",") if x.strip()]
    except ValueError:
        raise HTTPException(status_code=400, detail="录音 ID 格式无效")
    if not id_list:
        raise HTTPException(status_code=400, detail="未选择录音")
    
    try:
        result = await db.execute(select(Recording).where(Recording.id.in_(id_list)).order_by(Recording.created_at.asc()))
        recs = result.scalars().all()
    except Exception as e:
        logger.error(f"批量导出查询失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="查询录音数据失败")
    
    if not recs:
        raise HTTPException(status_code=404, detail="未找到指定的录音记录")

    try:
        # 生成 ZIP 文件
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
            for rec in recs:
                try:
                    # 为每个录音生成 markdown 文件
                    # 安全文件名（基础部分）
                    base_name = (rec.title or rec.original_filename or f"recording_{rec.id}").replace(" ", "_").replace("/", "_")
                    base_name = re.sub(r"[<>:\"'/\\|?*]", "_", base_name)

                    # --- 摘要文件 ---
                    meta_parts = [
                        f"# {rec.title}",
                        "",
                        f"- **日期**: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}" if rec.created_at else "",
                        f"- **时长**: {format_duration(rec.duration)}" if rec.duration else "",
                        f"- **状态**: {rec.status}",
                    ]
                    if rec.keywords:
                        meta_parts.append(f"- **关键词**: {', '.join(rec.keywords)}")
                    meta_parts.extend(["", "---", "", "## 会议纪要", "", rec.summary_text or "（暂无摘要）", ""])
                    if rec.action_items:
                        meta_parts.append("## 待办事项")
                        meta_parts.append("")
                        for item in rec.action_items:
                            meta_parts.append(f"- {item}")
                        meta_parts.append("")
                    zf.writestr(f"{base_name}_摘要.md", "\n".join(meta_parts).encode('utf-8'))

                    # --- 逐字稿文件 ---
                    transcript_parts = [
                        f"# {rec.title}",
                        "",
                        f"- **日期**: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}" if rec.created_at else "",
                        f"- **时长**: {format_duration(rec.duration)}" if rec.duration else "",
                        "",
                        "## 逐字稿",
                        "",
                        rec.transcript_text or "（暂无逐字稿）",
                    ]
                    zf.writestr(f"{base_name}_逐字稿.md", "\n".join(transcript_parts).encode('utf-8'))
                except Exception as e:
                    logger.warning(f"批量导出: 录音 {rec.id} 生成文件失败，跳过: {e}")
                    continue
        
        buf.seek(0)
        filename = quote("批量导出_会议纪要.zip")
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"批量导出 ZIP 生成失败: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="批量导出失败，请稍后重试")


@router.post("/batch-export-zip")
async def batch_export_zip(
    ids: list[int],
    db: AsyncSession = Depends(get_db),
):
    """批量导出选中的录音为 ZIP 文件（POST 接口，支持更多 ID）。"""
    if not ids:
        raise HTTPException(status_code=400, detail="未选择录音")
    result = await db.execute(select(Recording).where(Recording.id.in_(ids)).order_by(Recording.created_at.asc()))
    recs = result.scalars().all()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rec in recs:
            # 安全文件名（基础部分）
            base_name = (rec.title or rec.original_filename or f"recording_{rec.id}").replace(" ", "_").replace("/", "_")
            base_name = re.sub(r"[<>:\"'/\\|?*]", "_", base_name)

            # --- 摘要文件 ---
            meta_parts = [
                f"# {rec.title}",
                "",
                f"- **日期**: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}" if rec.created_at else "",
                f"- **时长**: {format_duration(rec.duration)}" if rec.duration else "",
                f"- **状态**: {rec.status}",
            ]
            if rec.keywords:
                meta_parts.append(f"- **关键词**: {', '.join(rec.keywords)}")
            meta_parts.extend(["", "---", "", "## 会议纪要", "", rec.summary_text or "（暂无摘要）", ""])
            if rec.action_items:
                meta_parts.append("## 待办事项")
                meta_parts.append("")
                for item in rec.action_items:
                    meta_parts.append(f"- {item}")
                meta_parts.append("")
            zf.writestr(f"{base_name}_摘要.md", "\n".join(meta_parts).encode('utf-8'))

            # --- 逐字稿文件 ---
            transcript_parts = [
                f"# {rec.title}",
                "",
                f"- **日期**: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}" if rec.created_at else "",
                f"- **时长**: {format_duration(rec.duration)}" if rec.duration else "",
                "",
                "## 逐字稿",
                "",
                rec.transcript_text or "（暂无逐字稿）",
            ]
            zf.writestr(f"{base_name}_逐字稿.md", "\n".join(transcript_parts).encode('utf-8'))
    
    buf.seek(0)
    filename = quote("批量导出_会议纪要.zip")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"},
    )


@router.get("/{rec_id}", response_model=RecordingDetail)
async def get_recording(rec_id: int, db: AsyncSession = Depends(get_db)):
    """获取录音详情。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    return RecordingDetail.model_validate(rec)


@router.put("/{rec_id}/transcript", response_model=RecordingDetail)
async def edit_transcript(
    rec_id: int,
    req: TranscriptEditRequest,
    db: AsyncSession = Depends(get_db),
):
    """编辑转录稿段落。

    支持三种操作（可组合）：
    - segments: 完整替换所有段落
    - patch: 按索引增量修改单个段落
    - speaker_names: 批量重命名说话人
    """
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_segments:
        raise HTTPException(status_code=400, detail="暂无转录段落，无法编辑")

    segments = rec.transcript_segments
    if not isinstance(segments, list):
        segments = []

    # 1. 完整替换
    if req.segments is not None:
        segments = req.segments

    # 2. 增量修改
    if req.patch:
        for edit in req.patch:
            if 0 <= edit.index < len(segments):
                seg = segments[edit.index]
                if not isinstance(seg, dict):
                    seg = {}
                if edit.text is not None:
                    seg["text"] = edit.text
                if edit.speaker is not None:
                    seg["speaker"] = edit.speaker
                if edit.start is not None:
                    seg["start"] = edit.start
                if edit.end is not None:
                    seg["end"] = edit.end
                segments[edit.index] = seg

    # 3. 说话人重命名
    if req.speaker_names:
        for seg in segments:
            if isinstance(seg, dict) and seg.get("speaker") in req.speaker_names:
                seg["speaker"] = req.speaker_names[seg["speaker"]]

    rec.transcript_segments = segments
    rec.transcript_text = " ".join(
        seg.get("text", "") for seg in segments if isinstance(seg, dict)
    )

    # 保存到文件
    rec.transcript_path = await storage.save_text(
        rec.transcript_text, rec_id, "transcripts", "txt"
    )
    await db.commit()
    await db.refresh(rec)
    logger.info(f"转录稿已编辑: id={rec_id}")
    # 异步重新索引段级向量
    try:
        await _ensure_segment_embeddings(rec_id)
    except Exception as seg_err:
        logger.warning(f"段级 Embedding 重新索引失败（不影响编辑结果）: id={rec_id}, error={seg_err}")
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/transcript/merge", response_model=RecordingDetail)
async def merge_segments(
    rec_id: int,
    req: SegmentMergeRequest,
    db: AsyncSession = Depends(get_db),
):
    """合并多个转录段落为一个。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_segments:
        raise HTTPException(status_code=400, detail="暂无转录段落，无法合并")

    segments = rec.transcript_segments
    if not isinstance(segments, list):
        raise HTTPException(status_code=400, detail="转录段落数据异常")

    if len(req.indices) < 2:
        raise HTTPException(status_code=400, detail="至少需要2个段落才能合并")

    # 验证索引合法性
    for idx in req.indices:
        if idx < 0 or idx >= len(segments):
            raise HTTPException(status_code=400, detail=f"段落索引 {idx} 超出范围")

    # 取出要合并的段落
    to_merge = [segments[i] for i in req.indices]
    first = to_merge[0]
    last = to_merge[-1]

    merged_text = " ".join(seg.get("text", "") for seg in to_merge if isinstance(seg, dict))
    merged_speaker = req.speaker if req.speaker else (first.get("speaker") if isinstance(first, dict) else None)
    merged_start = first.get("start") if isinstance(first, dict) else None
    merged_end = last.get("end") if isinstance(last, dict) else None

    merged_seg = {
        "text": merged_text,
        "speaker": merged_speaker,
        "start": merged_start,
        "end": merged_end,
    }

    # 构建新段落列表：替换被合并的段落
    new_segments = []
    merge_set = set(req.indices)
    merged_inserted = False
    for i, seg in enumerate(segments):
        if i in merge_set:
            if not merged_inserted:
                new_segments.append(merged_seg)
                merged_inserted = True
            # 跳过其他被合并的段落
        else:
            new_segments.append(seg)

    rec.transcript_segments = new_segments
    rec.transcript_text = " ".join(
        seg.get("text", "") for seg in new_segments if isinstance(seg, dict)
    )

    rec.transcript_path = await storage.save_text(
        rec.transcript_text, rec_id, "transcripts", "txt"
    )
    await db.commit()
    await db.refresh(rec)
    logger.info(f"段落已合并: id={rec_id}, indices={req.indices}")
    # 异步重新索引段级向量
    try:
        await _ensure_segment_embeddings(rec_id)
    except Exception as seg_err:
        logger.warning(f"段级 Embedding 重新索引失败（不影响合并结果）: id={rec_id}, error={seg_err}")
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/transcript/split", response_model=RecordingDetail)
async def split_segment(
    rec_id: int,
    req: SegmentSplitRequest,
    db: AsyncSession = Depends(get_db),
):
    """拆分一个转录段落为两个。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_segments:
        raise HTTPException(status_code=400, detail="暂无转录段落，无法拆分")

    segments = rec.transcript_segments
    if not isinstance(segments, list):
        raise HTTPException(status_code=400, detail="转录段落数据异常")

    if req.index < 0 or req.index >= len(segments):
        raise HTTPException(status_code=400, detail=f"段落索引 {req.index} 超出范围")

    seg = segments[req.index]
    if not isinstance(seg, dict):
        raise HTTPException(status_code=400, detail="段落数据异常")

    text = seg.get("text", "")
    pos = req.position
    if pos < 0 or pos > len(text):
        raise HTTPException(status_code=400, detail=f"拆分位置 {pos} 超出文本长度 {len(text)}")

    text1 = text[:pos].rstrip()
    text2 = text[pos:].lstrip()

    start = seg.get("start")
    end = seg.get("end")
    speaker = seg.get("speaker")

    # 按文本长度比例分配时间
    total_len = len(text) if len(text) > 0 else 1
    ratio = pos / total_len if total_len > 0 else 0.5

    if start is not None and end is not None:
        duration = end - start
        mid = start + duration * ratio
    else:
        mid = None
        start = None
        end = None

    seg1 = {
        "text": text1,
        "speaker": speaker,
        "start": start,
        "end": mid,
    }
    seg2 = {
        "text": text2,
        "speaker": req.speaker if req.speaker else speaker,
        "start": mid,
        "end": end,
    }

    # 构建新段落列表
    new_segments = segments[:req.index] + [seg1, seg2] + segments[req.index + 1:]

    rec.transcript_segments = new_segments
    rec.transcript_text = " ".join(
        seg.get("text", "") for seg in new_segments if isinstance(seg, dict)
    )

    rec.transcript_path = await storage.save_text(
        rec.transcript_text, rec_id, "transcripts", "txt"
    )
    await db.commit()
    await db.refresh(rec)
    logger.info(f"段落已拆分: id={rec_id}, index={req.index}, position={req.position}")
    # 异步重新索引段级向量
    try:
        await _ensure_segment_embeddings(rec_id)
    except Exception as seg_err:
        logger.warning(f"段级 Embedding 重新索引失败（不影响拆分结果）: id={rec_id}, error={seg_err}")
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/transcript/regenerate-summary", response_model=RecordingDetail)
async def regenerate_summary_from_transcript(
    rec_id: int,
    background_tasks: BackgroundTasks,
    req: RegenerateRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """基于编辑后的转录稿重新生成摘要。

    与 /{rec_id}/summarize 功能一致，但语义上更明确：用于转录稿编辑后重新生成摘要。
    """
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_text:
        raise HTTPException(status_code=400, detail="暂无转录文本，无法生成摘要")

    req = req or RegenerateRequest()

    # meeting_type_id: 正整数 → 设置关联；null → 清除关联；不传 → 保持现状
    if req.meeting_type_id is not None:
        mt = await db.get(MeetingType, req.meeting_type_id)
        if not mt:
            raise HTTPException(status_code=404, detail="会议类型不存在")
        rec.meeting_type_id = req.meeting_type_id

    model_config = await get_model_config_by_id(req.model_id, db)
    logger.info(f"基于编辑后转录稿重新生成摘要, rec_id={rec_id}, model_id={req.model_id}, model={model_config.model if model_config else '全局默认'}, sub_type={req.sub_type}")

    rec.status = "summarizing"
    rec.error_message = None
    await db.commit()
    await db.refresh(rec)
    background_tasks.add_task(run_summary, rec_id, model_config, req.sub_type)
    return RecordingDetail.model_validate(rec)


def _serve_audio_range(file_path: str, media_type: str, filename: str, request: Request) -> Response:
    """返回支持 Range 请求的音频响应，允许浏览器 <audio> 拖动进度条 seek。"""
    file_size = os.path.getsize(file_path)
    range_header = request.headers.get("range")

    if range_header:
        # 解析 Range: bytes=start-end
        m = re.match(r"bytes=(\d+)-(\d*)", range_header)
        if m:
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else file_size - 1
            end = min(end, file_size - 1)
            content_length = end - start + 1

            def iter_file():
                with open(file_path, "rb") as f:
                    f.seek(start)
                    remaining = content_length
                    while remaining > 0:
                        chunk = f.read(min(64 * 1024, remaining))
                        if not chunk:
                            break
                        remaining -= len(chunk)
                        yield chunk

            return StreamingResponse(
                iter_file(),
                media_type=media_type,
                status_code=206,
                headers={
                    "Content-Range": f"bytes {start}-{end}/{file_size}",
                    "Accept-Ranges": "bytes",
                    "Content-Length": str(content_length),
                    "Cache-Control": "public, max-age=3600",
                },
            )

    # 无 Range 头或格式异常 → 返回完整文件
    return FileResponse(file_path, media_type=media_type, filename=filename)


@router.get("/{rec_id}/audio")
async def get_audio(rec_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """获取音频文件流（支持 Range 请求，允许拖动进度条）。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not os.path.exists(rec.audio_path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return _serve_audio_range(rec.audio_path, rec.content_type or "audio/mpeg", rec.original_filename, request)


@router.get("/{rec_id}/download")
async def download_audio(rec_id: int, db: AsyncSession = Depends(get_db)):
    """下载原始录音文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not os.path.exists(rec.audio_path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    
    safe_name = (rec.original_filename or f"recording_{rec.id}").replace(' ', '_').replace('/', '_')
    safe_name = re.sub(r'[<>:"/\\|?*]', '_', safe_name)
    encoded_name = quote(safe_name)
    
    return FileResponse(
        rec.audio_path,
        media_type=rec.content_type or "application/octet-stream",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"},
    )


@router.patch("/{rec_id}/title", response_model=RecordingOut)
async def rename_recording(
    rec_id: int,
    req: RenameRequest,
    db: AsyncSession = Depends(get_db),
):
    """修改录音标题。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    title = req.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    rec.title = title
    await db.commit()
    await db.refresh(rec)
    logger.info(f"录音已重命名: id={rec_id}, new_title={title}")
    return RecordingOut.model_validate(rec)


@router.post("/{rec_id}/summarize", response_model=RecordingDetail)
async def re_summarize(
    rec_id: int,
    background_tasks: BackgroundTasks,
    req: RegenerateRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """重新生成摘要。支持指定会议类型和模型。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_text:
        raise HTTPException(status_code=400, detail="暂无转录文本，无法生成摘要")
    
    req = req or RegenerateRequest()
    
    # meeting_type_id: null → 清除会议类型关联，正整数 → 设置关联
    if req.meeting_type_id is not None:
        mt = await db.get(MeetingType, req.meeting_type_id)
        if not mt:
            raise HTTPException(status_code=404, detail="会议类型不存在")
        rec.meeting_type_id = req.meeting_type_id
    else:
        # 显式传 null 表示使用默认模板，清除关联
        rec.meeting_type_id = None
    
    # 获取模型配置
    model_config = await get_model_config_by_id(req.model_id, db)
    logger.info(f"重新生成摘要, rec_id={rec_id}, model_id={req.model_id}, model={model_config.model if model_config else '全局默认'}, sub_type={req.sub_type}")
    
    rec.status = "summarizing"
    rec.error_message = None
    await db.commit()
    await db.refresh(rec)
    background_tasks.add_task(run_summary, rec_id, model_config, req.sub_type)
    return RecordingDetail.model_validate(rec)


@router.post("/batch-delete")
async def batch_delete(
    req: BatchDeleteRequest,
    db: AsyncSession = Depends(get_db),
):
    """批量删除录音。"""
    if not req.ids:
        raise HTTPException(status_code=400, detail="未选择录音")
    result = await db.execute(select(Recording).where(Recording.id.in_(req.ids)))
    recs = result.scalars().all()
    deleted = 0
    for rec in recs:
        storage.delete_file_safe(rec.audio_path)
        if rec.transcript_path:
            storage.delete_file_safe(rec.transcript_path)
        if rec.summary_path:
            storage.delete_file_safe(rec.summary_path)
        await db.delete(rec)
        deleted += 1
    # 同步删除 Zvec 中的向量
    from app.services.vector_store import vector_store
    if vector_store.is_ready:
        for rid in req.ids:
            vector_store.delete(f"rec_{rid}")
            vector_store.delete_segments_by_recording(rid)
    await db.commit()
    logger.info(f"批量删除: {deleted} 条录音")
    return {"detail": f"已删除 {deleted} 条录音"}


@router.delete("/{rec_id}")
async def delete_recording(rec_id: int, db: AsyncSession = Depends(get_db)):
    """删除录音及其所有衍生文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    storage.delete_file_safe(rec.audio_path)
    if rec.transcript_path:
        storage.delete_file_safe(rec.transcript_path)
    if rec.summary_path:
        storage.delete_file_safe(rec.summary_path)
    # 同步删除 Zvec 中的向量
    from app.services.vector_store import vector_store
    if vector_store.is_ready:
        vector_store.delete(f"rec_{rec_id}")
        vector_store.delete_segments_by_recording(rec_id)
    await db.delete(rec)
    await db.commit()
    logger.info(f"录音已删除: id={rec_id}")
    return {"detail": "deleted"}


@router.get("/{rec_id}/export", response_class=PlainTextResponse)
async def export_recording(rec_id: int, db: AsyncSession = Depends(get_db)):
    """导出单个录音为 Markdown 文件。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")

    parts = [
        f"# {rec.title}",
        "",
        f"- **日期**: {format_datetime(rec.created_at, '%Y-%m-%d %H:%M')}" if rec.created_at else "",
        f"- **时长**: {format_duration(rec.duration)}" if rec.duration else "",
        f"- **语言**: {rec.language}" if rec.language else "",
        f"- **状态**: {rec.status}",
    ]
    if rec.keywords:
        parts.append(f"- **关键词**: {', '.join(rec.keywords)}")
    parts.extend([
        "",
        "---",
        "",
        "## 会议纪要",
        "",
        rec.summary_text or "（暂无摘要）",
        "",
    ])
    if rec.action_items:
        parts.append("## 待办事项")
        parts.append("")
        for item in rec.action_items:
            parts.append(f"- {item}")
        parts.append("")
    parts.extend([
        "---",
        "",
        "## 逐字稿",
        "",
        rec.transcript_text or "（暂无逐字稿）",
        "",
    ])
    content = "\n".join(parts)
    safe_name = (rec.title or rec.original_filename).replace(" ", "_").replace("/", "_")
    encoded_name = quote(f"{safe_name}.md")
    return PlainTextResponse(
        content=content,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_name}"
        },
        media_type="text/markdown; charset=utf-8",
    )


@router.post("/{rec_id}/mindmap", response_model=RecordingDetail)
async def generate_mindmap(
    rec_id: int,
    background_tasks: BackgroundTasks,
    req: RegenerateMindmapRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """生成思维导图。支持指定模型。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.summary_text:
        raise HTTPException(status_code=400, detail="暂无会议总结，无法生成思维导图")
    
    req = req or RegenerateMindmapRequest()
    model_config = await get_model_config_by_id(req.model_id, db)
    logger.info(f"重新生成思维导图, rec_id={rec_id}, model_id={req.model_id}, model={model_config.model if model_config else '全局默认'}")
    
    rec.error_message = None
    await db.commit()
    await db.refresh(rec)
    background_tasks.add_task(run_mindmap, rec_id, model_config)
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/action-items", response_model=RecordingDetail)
async def regenerate_action_items(
    rec_id: int,
    background_tasks: BackgroundTasks,
    req: RegenerateActionItemsRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """重新生成待办事项（基于会议总结）。支持指定模型。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.summary_text:
        raise HTTPException(status_code=400, detail="暂无会议总结，无法生成待办事项")
    
    req = req or RegenerateActionItemsRequest()
    model_config = await get_model_config_by_id(req.model_id, db)
    logger.info(f"重新生成待办事项, rec_id={rec_id}, model_id={req.model_id}, model={model_config.model if model_config else '全局默认'}")
    
    rec.error_message = None
    await db.commit()
    await db.refresh(rec)
    background_tasks.add_task(run_action_items, rec_id, model_config)
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/keywords", response_model=RecordingDetail)
async def regenerate_keywords(
    rec_id: int,
    background_tasks: BackgroundTasks,
    req: RegenerateKeywordsRequest | None = None,
    db: AsyncSession = Depends(get_db),
):
    """重新生成关键词与标签（基于会议总结）。支持指定模型。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.summary_text:
        raise HTTPException(status_code=400, detail="暂无会议总结，无法生成关键词")
    
    req = req or RegenerateKeywordsRequest()
    model_config = await get_model_config_by_id(req.model_id, db)
    logger.info(f"重新生成关键词与标签, rec_id={rec_id}, model_id={req.model_id}, model={model_config.model if model_config else '全局默认'}")
    
    rec.error_message = None
    await db.commit()
    await db.refresh(rec)
    background_tasks.add_task(run_keywords, rec_id, model_config)
    return RecordingDetail.model_validate(rec)


@router.post("/{rec_id}/share", response_model=dict)
async def create_share_link(
    rec_id: int,
    db: AsyncSession = Depends(get_db),
):
    """生成只读分享链接。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    token = hashlib.sha256(f"{rec_id}:{settings.share_secret}:{rec.created_at}".encode()).hexdigest()[:32]
    rec.is_shared = True
    rec.share_token = token
    await db.commit()
    return {"share_token": token, "share_url": f"/share/{token}"}


@router.delete("/{rec_id}/share")
async def revoke_share_link(
    rec_id: int,
    db: AsyncSession = Depends(get_db),
):
    """撤销分享链接。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    rec.is_shared = False
    rec.share_token = None
    await db.commit()
    return {"detail": "分享已撤销"}


@router.get("/audio-share/{share_token}")
async def get_audio_by_share_token(
    share_token: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """通过分享 token 获取音频文件流（公开接口，无需登录，支持 Range）。"""
    result = await db.execute(
        select(Recording).where(
            Recording.share_token == share_token,
            Recording.is_shared == True,
        )
    )
    rec = result.scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")
    if not os.path.exists(rec.audio_path):
        raise HTTPException(status_code=404, detail="音频文件不存在")
    return _serve_audio_range(rec.audio_path, rec.content_type or "audio/mpeg", rec.original_filename, request)


# ---------------- SRT 上传（需求8） ----------------

def _parse_srt_time(time_str: str) -> float:
    """将 SRT 时间戳 (HH:MM:SS,mmm) 转换为秒。"""
    time_str = time_str.strip().replace(',', '.')
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        try:
            return float(time_str)
        except ValueError:
            return 0.0


def parse_srt(content: str) -> tuple[str, list[dict]]:
    """解析 SRT 格式为纯文本和段落列表。"""
    segments = []
    text_parts = []
    # 标准化换行符
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    blocks = content.strip().split('\n\n')
    
    for block in blocks:
        lines = block.strip().split('\n')
        if len(lines) < 2:
            continue
        
        # 跳过序号行（纯数字）
        idx = 0
        if lines[0].strip().isdigit():
            idx = 1
        
        if idx >= len(lines):
            continue
        
        # 解析时间戳行
        time_match = re.match(
            r'(\d{2}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}[,.]\d{3})',
            lines[idx]
        )
        if not time_match:
            continue
        
        start = _parse_srt_time(time_match.group(1))
        end = _parse_srt_time(time_match.group(2))
        
        # 剩余行是字幕文本
        text_lines = lines[idx + 1:]
        text = ' '.join(line.strip() for line in text_lines if line.strip())
        if not text:
            continue
        
        segments.append({
            "text": text,
            "start": start,
            "end": end,
            "speaker": None,
        })
        text_parts.append(text)
    
    return ' '.join(text_parts), segments


def _parse_txt_time(time_str: str) -> float:
    """将 TXT 时间戳 (MM:SS 或 H:MM:SS 或 HH:MM:SS) 转换为秒。"""
    time_str = time_str.strip()
    parts = time_str.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    elif len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    else:
        try:
            return float(time_str)
        except ValueError:
            return 0.0


def parse_txt(content: str) -> tuple[str, list[dict]]:
    """解析腾讯云转写结果 TXT 格式为纯文本和段落列表。

    格式示例:
      说话人1 00:00
      转录文本（可多行）
      说话人2 00:09
      转录文本
      ...
      以上内容由AI生成
    """
    segments = []
    text_parts = []
    content = content.replace('\r\n', '\n').replace('\r', '\n')
    lines = content.split('\n')

    # 正则匹配 "说话人N 时间戳" 行
    speaker_re = re.compile(r'^说话人(\d+)\s+(\d{1,2}:\d{2}(?::\d{2})?)\s*$')

    current_speaker = None
    current_start = 0.0
    current_text_lines = []

    def flush_segment():
        nonlocal current_speaker, current_start, current_text_lines
        if current_speaker is not None and current_text_lines:
            text = ' '.join(l.strip() for l in current_text_lines if l.strip())
            if text:
                segments.append({
                    "text": text,
                    "start": current_start,
                    "end": current_start,  # TXT 无结束时间，用 start 代替
                    "speaker": f"说话人{current_speaker}",
                })
                text_parts.append(text)
        current_speaker = None
        current_start = 0.0
        current_text_lines = []

    for line in lines:
        stripped = line.strip()
        # 跳过空行
        if not stripped:
            continue
        # 跳过文件头（文件名行、"转写结果" 标题）
        if stripped == '转写结果':
            continue
        # 跳过文件尾标记
        if stripped.startswith('以上内容由'):
            continue
        # 跳过纯文件名行（含扩展名，通常在第一行）
        if re.match(r'^.+\.\w{2,4}$', stripped) and not speaker_re.match(stripped):
            continue

        m = speaker_re.match(stripped)
        if m:
            # 新的说话人段，先保存上一段
            flush_segment()
            current_speaker = int(m.group(1))
            current_start = _parse_txt_time(m.group(2))
        else:
            # 文本行（可能属于当前说话人段）
            if current_speaker is not None:
                current_text_lines.append(stripped)
            # 如果还没遇到说话人行，跳过（文件头等）

    flush_segment()

    return ' '.join(text_parts), segments


def parse_transcript_file(content: str, filename: str) -> tuple[str, list[dict]]:
    """根据文件扩展名自动选择解析器。"""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".txt":
        return parse_txt(content)
    else:
        return parse_srt(content)


@router.post("/{rec_id}/upload-srt", response_model=RecordingDetail)
async def upload_srt(
    rec_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """上传字幕/转录文件（SRT 或 TXT），解析后自动触发生成摘要、关键词、待办事项等。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    
    # 读取并解析文件
    content_bytes = await file.read()
    # 尝试 UTF-8，回退 GBK
    try:
        content = content_bytes.decode('utf-8-sig')  # utf-8-sig 自动去除 BOM
    except UnicodeDecodeError:
        try:
            content = content_bytes.decode('gbk')
        except UnicodeDecodeError:
            content = content_bytes.decode('utf-8', errors='replace')
    
    # 根据文件扩展名自动选择解析器
    transcript_text, segments = parse_transcript_file(content, file.filename or "")
    ext = os.path.splitext(file.filename or "")[1].lower()
    file_label = "TXT" if ext == ".txt" else "SRT"
    if not transcript_text:
        raise HTTPException(status_code=400, detail=f"{file_label} 文件解析失败，未找到有效内容")
    
    rec.transcript_text = transcript_text
    rec.transcript_segments = segments
    rec.status = "transcribed"
    rec.transcript_path = await storage.save_text(transcript_text, rec_id, "transcripts", "txt")
    
    # 如果有音频文件，尝试获取时长
    if rec.audio_path and os.path.exists(rec.audio_path) and not rec.duration:
        try:
            import mutagen
            audio = mutagen.File(rec.audio_path)
            if audio and audio.info:
                rec.duration = audio.info.length
        except Exception:
            pass
    # 从 SRT 字幕时长补充录音时长
    if segments and (not rec.duration or rec.duration == 0):
        rec.duration = segments[-1]["end"]

    await db.commit()
    await db.refresh(rec)
    logger.info(f"{file_label} 上传成功: id={rec_id}, segments={len(segments)}")
    
    # 自动触发摘要生成
    model_config = await get_model_config_by_id(None, db)
    background_tasks.add_task(run_summary, rec_id, model_config)
    
    return RecordingDetail.model_validate(rec)


# ---------------- 备注管理 ----------------

@router.put("/{rec_id}/notes", response_model=RecordingDetail)
async def update_notes(
    rec_id: int,
    notes: str = Form(...),
    db: AsyncSession = Depends(get_db),
):
    """保存录音备注（Markdown 格式），保存后自动更新 embedding 向量。"""
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    
    rec.notes = notes.strip() if notes else None
    await db.commit()
    await db.refresh(rec)
    logger.info(f"备注已保存: id={rec_id}, length={len(rec.notes or '')}")
    
    # 异步更新 embedding（备注内容纳入向量化）
    try:
        await _ensure_embedding(rec_id)
    except Exception as e:
        logger.warning(f"备注保存后更新 embedding 失败: id={rec_id}, error={e}")
    
    return RecordingDetail.model_validate(rec)


# ---------------- 直接转录（需求3） ----------------

@router.post("/{rec_id}/transcribe", response_model=RecordingDetail)
async def transcribe_recording(
    rec_id: int,
    background_tasks: BackgroundTasks,
    engine: str = "qwen_asr",
    asr_provider_id: int | None = None,
    db: AsyncSession = Depends(get_db),
):
    """直接发起转录（不通过队列），支持选择转录引擎和 ASR 提供商。

    Args:
        engine: 转录引擎，qwen_asr (默认)
        asr_provider_id: ASR 提供商 ID（选传，指定后更新录音的 asr_provider_id）
    """
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")

    if engine not in ("qwen_asr",):
        raise HTTPException(status_code=400, detail=f"不支持的转录引擎: {engine}")

    if rec.status in ("transcribing", "summarizing"):
        raise HTTPException(status_code=400, detail="当前正在处理中，请等待完成")

    rec.status = "pending"
    rec.error_message = None
    rec.engine = engine
    # 如果前端指定了 ASR 提供商，更新录音的 provider 绑定
    if asr_provider_id is not None:
        rec.asr_provider_id = asr_provider_id
    await db.commit()
    await db.refresh(rec)

    logger.info(f"直接转录, rec_id={rec_id}, engine={engine}, asr_provider_id={asr_provider_id}")
    background_tasks.add_task(process_transcription, rec_id, engine)
    return RecordingDetail.model_validate(rec)


# ---------------- 工具函数 ----------------

def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "未知"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}分{s}秒"


async def _get_default_model_config_safe() -> ModelConfig | None:
    """安全获取数据库默认模型配置，失败时返回 None（回退全局配置）。"""
    try:
        async with async_session() as db:
            from app.routes.models import get_model_config_by_id
            return await get_model_config_by_id(None, db)
    except Exception as e:
        logger.warning(f"获取数据库默认模型失败，回退全局配置: {e}")
        return None


async def _ensure_embedding(rec_id: int):
    """为录音生成 embedding 向量并写入 Zvec 向量数据库（如已配置 embedding 模型）。
    
    embedding 文本 = 标题 + 会议纪要 + 备注 + 关键词，避免逐字稿过长导致超出上下文限制。
    """
    from app.services.embedding import get_embedding, EmbeddingModelConfig
    from app.services.vector_store import vector_store
    from app.models import LLMModel

    async with async_session() as db:
        # 获取 embedding 模型配置
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.model_type == "embedding",
                LLMModel.is_enabled == True,
            ).order_by(LLMModel.is_default.desc(), LLMModel.sort_order.asc())
        )
        model = result.scalars().first()
        if not model:
            return  # 未配置 embedding 模型，跳过

        rec = await db.get(Recording, rec_id)
        if not rec:
            return
        
        # embedding 文本：标题 + 纪要 + 备注 + 关键词
        parts = []
        if rec.title:
            parts.append(rec.title)
        if rec.summary_text:
            parts.append(rec.summary_text)
        if rec.notes:
            parts.append(rec.notes)
        if not rec.summary_text and not rec.notes:
            # 纪要和备注都为空时，回退到关键词
            if rec.keywords:
                parts.extend(rec.keywords)
        emb_text = ' '.join(p for p in parts if p)
        
        if not emb_text:
            logger.info(f"录音 {rec_id} 无可嵌入文本，跳过 embedding")
            return
        
        config = EmbeddingModelConfig(
            base_url=model.base_url,
            api_key=model.api_key,
            model=model.model_id,
            max_context_length=model.max_context_length or 8000,
        )
        vec = await get_embedding(emb_text, config)

        # 确保维度匹配
        await vector_store.ensure_dim(len(vec))

        # 写入 Zvec
        doc_id = f"rec_{rec_id}"
        created_ts = rec.created_at.timestamp() if rec.created_at else 0.0
        vector_store.insert(
            doc_id=doc_id,
            recording_id=rec_id,
            embedding=vec,
            title=rec.title or rec.original_filename or "",
            created_at_ts=created_ts,
        )
        vector_store.flush()
        logger.info(f"Embedding 已写入 Zvec: rec_id={rec_id}, dim={len(vec)}")


async def _ensure_segment_embeddings(rec_id: int):
    """为录音的 transcript_segments 逐段生成 embedding 并写入 Zvec 段级 collection。

    如果 embedding 模型未配置则静默跳过。
    """
    from app.services.embedding import get_embeddings_batch, EmbeddingModelConfig
    from app.services.vector_store import vector_store
    from app.models import LLMModel

    async with async_session() as db:
        # 获取 embedding 模型配置
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.model_type == "embedding",
                LLMModel.is_enabled == True,
            ).order_by(LLMModel.is_default.desc(), LLMModel.sort_order.asc())
        )
        model = result.scalars().first()
        if not model:
            return  # 未配置 embedding 模型，跳过

        rec = await db.get(Recording, rec_id)
        if not rec or not rec.transcript_segments:
            return

        segs = _get_segments(rec)
        if not segs:
            return

        # 提取各段文本，过滤空段
        seg_texts = [s.get("text", "").strip() for s in segs]
        # 记录索引，用于后续写入
        valid_indices = [i for i, t in enumerate(seg_texts) if t]
        valid_texts = [seg_texts[i] for i in valid_indices]
        if not valid_texts:
            return

        config = EmbeddingModelConfig(
            base_url=model.base_url,
            api_key=model.api_key,
            model=model.model_id,
            max_context_length=model.max_context_length or 8000,
        )

        # 批量生成 embedding
        vectors = await get_embeddings_batch(valid_texts, config)
        if not vectors:
            return

        # 确保维度匹配
        await vector_store.ensure_dim(len(vectors[0]))

        # 写入 Zvec 段级 collection
        for idx_in_batch, orig_idx in enumerate(valid_indices):
            seg = segs[orig_idx]
            doc_id = f"seg_{rec_id}_{orig_idx}"
            vector_store.insert_segment(
                doc_id=doc_id,
                recording_id=rec_id,
                segment_index=orig_idx,
                start=seg.get("start", 0.0),
                end=seg.get("end", 0.0),
                speaker=seg.get("speaker", ""),
                embedding=vectors[idx_in_batch],
            )

        vector_store.flush_segments()
        logger.info(f"段级 Embedding 已写入 Zvec: rec_id={rec_id}, segments={len(valid_indices)}")


# ---------------- 后台任务 ----------------

async def process_transcription(rec_id: int, engine: str = "qwen_asr"):
    """后台任务：调用 ASR 转录音频。

    Args:
        engine: 转录引擎，qwen_asr (默认)
    """
    logger.info(f"process_transcription 开始, rec_id={rec_id}, engine={engine}")
    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec:
            return
        rec.status = "transcribing"
        rec.engine = engine
        await db.commit()
        try:
            # 构建热词库参数
            vocabulary_id = None
            if rec.hotword_library_id:
                result_hotwords = await db.execute(
                    select(Hotword).where(Hotword.library_id == rec.hotword_library_id)
                )
                hotwords = result_hotwords.scalars().all()
                if hotwords:
                    parts = [f"{hw.word} {hw.weight}" for hw in hotwords]
                    vocabulary_id = " ".join(parts)
                    logger.info(f"录音{rec_id}使用热词库{rec.hotword_library_id}: {len(hotwords)}个热词")

            result = await asr.transcribe_audio(
                file_path=rec.audio_path,
                filename=rec.original_filename,
                content_type=rec.content_type,
                provider_id=rec.asr_provider_id,
                vocabulary_id=vocabulary_id,
                db=db,
            )
            rec.transcript_text = result.get("text")
            rec.transcript_segments = result.get("segments")
            rec.language = result.get("language")
            rec.duration = result.get("duration")
            rec.status = "transcribed"

            if rec.transcript_text:
                rec.transcript_path = await storage.save_text(
                    rec.transcript_text, rec_id, "transcripts", "txt"
                )

            await db.commit()
            logger.info(f"转录完成: id={rec_id}")
        except Exception as e:
            await db.rollback()
            rec = await db.get(Recording, rec_id)
            if rec:
                rec.status = "error"
                rec.error_message = f"ASR 转录失败: {e}"
                await db.commit()
            logger.error(f"转录失败: id={rec_id}, error={e}", exc_info=True)
            return

    await run_summary(rec_id, await _get_default_model_config_safe())

    if settings.webhook_url:
        await storage.notify_webhook(settings.webhook_url, rec_id, "transcription_done")


async def run_summary(rec_id: int, model_config: ModelConfig | None = None, sub_type: str | None = None):
    """后台任务：调用 LLM 生成摘要、关键词和待办事项。"""
    logger.info(f"run_summary 开始, rec_id={rec_id}, model={model_config.model if model_config else '全局默认'}, max_context={model_config.max_context_length if model_config else '默认'}, sub_type={sub_type}")
    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec or not rec.transcript_text:
            return
        rec.status = "summarizing"
        await db.commit()
        try:
            # 从会议类型加载 LLM 模板和细分类型
            custom_prompt = None
            resolved_sub_type = sub_type
            prompts = None
            if rec.meeting_type_id:
                mt = await db.get(MeetingType, rec.meeting_type_id)
                if mt:
                    # 加载 MeetingType 的三个 prompt 模板
                    prompts = {
                        "map": mt.map_prompt or None,
                        "reduce": mt.reduce_prompt or None,
                        "single_extract": mt.single_extract_prompt or None,
                    }
                    # 兼容旧字段 summary_prompt 作为额外要求
                    if mt.summary_prompt and mt.summary_prompt.strip():
                        custom_prompt = mt.summary_prompt
                        logger.info(f"录音{rec_id}使用会议类型{mt.name}的额外提示词")
                    if mt.sub_type and not resolved_sub_type:
                        resolved_sub_type = mt.sub_type
                        logger.info(f"录音{rec_id}使用会议类型{mt.name}的细分类型: {resolved_sub_type}")

            # 传入 segments 以支持按时间/说话人切块
            segments = _get_segments(rec)

            result = await llm.summarize_with_prompt(
                rec.transcript_text, custom_prompt, model_config,
                segments=segments, prompts=prompts, sub_type=resolved_sub_type
            )
            rec.summary_text = result.get("summary", "")
            rec.content_category = result.get("category", "meeting")
            rec.content_sub_type = result.get("sub_type", "regular")
            rec.action_items = result.get("action_items", [])
            rec.status = "done"

            if rec.summary_text:
                rec.summary_path = await storage.save_text(
                    rec.summary_text, rec_id, "summaries", "md"
                )

            await db.commit()
            logger.info(f"摘要生成完成: id={rec_id}, action_items={len(rec.action_items)}条")

            # 基于总结一次调用同时生成关键词和标签
            if rec.summary_text:
                try:
                    kw_tags = await llm.generate_keywords_and_tags(rec.summary_text, model_config)
                    rec.keywords = kw_tags.get("keywords", [])
                    rec.tags = kw_tags.get("tags", [])
                    await db.commit()
                    logger.info(f"关键词+标签生成完成: id={rec_id}, keywords={rec.keywords}, tags={rec.tags}")
                except Exception as kt_err:
                    logger.warning(f"关键词+标签生成失败（不影响摘要结果）: id={rec_id}, error={kt_err}")

            # 自动生成 embedding
            try:
                await _ensure_embedding(rec_id)
                logger.info(f"Embedding 生成完成: id={rec_id}")
            except Exception as emb_err:
                logger.warning(f"Embedding 生成失败（不影响摘要结果）: id={rec_id}, error={emb_err}")

            # 自动生成段级 embedding
            try:
                await _ensure_segment_embeddings(rec_id)
                logger.info(f"段级 Embedding 生成完成: id={rec_id}")
            except Exception as seg_err:
                logger.warning(f"段级 Embedding 生成失败（不影响摘要结果）: id={rec_id}, error={seg_err}")
        except Exception as e:
            await db.rollback()
            rec = await db.get(Recording, rec_id)
            if rec:
                rec.status = "error"
                rec.error_message = f"LLM 总结失败: {type(e).__name__}: {e}"
                await db.commit()
            logger.error(f"摘要生成失败: id={rec_id}, error={type(e).__name__}: {e}", exc_info=True)


async def run_mindmap(rec_id: int, model_config: ModelConfig | None = None):
    """后台任务：调用 LLM 生成思维导图。"""
    logger.info(f"run_mindmap 开始, rec_id={rec_id}, model={model_config.model if model_config else '全局默认'}")
    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec or not rec.summary_text:
            return
        try:
            mindmap = await llm.generate_mindmap(rec.summary_text, model_config)
            rec.mindmap_text = mindmap
            await db.commit()
            logger.info(f"思维导图生成完成: id={rec_id}")
        except Exception as e:
            await db.rollback()
            rec = await db.get(Recording, rec_id)
            if rec:
                rec.error_message = f"思维导图生成失败: {e}"
                await db.commit()
            logger.error(f"思维导图生成失败: id={rec_id}, error={e}", exc_info=True)


async def run_action_items(rec_id: int, model_config: ModelConfig | None = None):
    """后台任务：基于会议总结重新生成待办事项。"""
    logger.info(f"run_action_items 开始, rec_id={rec_id}, model={model_config.model if model_config else '全局默认'}")
    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec or not rec.summary_text:
            return
        try:
            items = await llm.extract_action_items_from_summary(rec.summary_text, model_config)
            rec.action_items = items
            await db.commit()
            logger.info(f"待办事项重新生成完成: id={rec_id}, count={len(items)}")
        except Exception as e:
            await db.rollback()
            rec = await db.get(Recording, rec_id)
            if rec:
                rec.error_message = f"待办事项生成失败: {e}"
                await db.commit()
            logger.error(f"待办事项生成失败: id={rec_id}, error={e}", exc_info=True)


async def run_keywords(rec_id: int, model_config: ModelConfig | None = None):
    """后台任务：基于会议总结重新生成关键词和标签（一次调用）。"""
    logger.info(f"run_keywords 开始, rec_id={rec_id}, model={model_config.model if model_config else '全局默认'}")
    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec or not rec.summary_text:
            return
        try:
            kw_tags = await llm.generate_keywords_and_tags(rec.summary_text, model_config)
            rec.keywords = kw_tags.get("keywords", [])
            rec.tags = kw_tags.get("tags", [])
            await db.commit()
            logger.info(f"关键词+标签重新生成完成: id={rec_id}, keywords={rec.keywords}, tags={rec.tags}")
        except Exception as e:
            await db.rollback()
            rec = await db.get(Recording, rec_id)
            if rec:
                rec.error_message = f"关键词生成失败: {e}"
                await db.commit()
            logger.error(f"关键词生成失败: id={rec_id}, error={e}", exc_info=True)


# ===== AI 对话 =====

@router.post("/{rec_id}/chat", response_model=ChatResponse)
async def chat_with_recording(
    rec_id: int,
    req: ChatRequest,
    db: AsyncSession = Depends(get_db),
):
    """与录音进行 AI 对话（RAG 增强）。

    检索相关原文段落作为上下文，支持多轮对话历史。
    """
    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_text:
        return ChatResponse(reply="该录音尚未完成转录，请先完成转录后再提问。")

    # 获取 chat 模型配置
    model_config = await get_model_config_by_id(req.model_id, db)
    if not model_config:
        return ChatResponse(reply="请先在模型管理中配置 LLM 模型。")

    # 获取 embedding 配置（用于检索）
    from app.services.embedding import EmbeddingModelConfig
    from app.routes.search import _get_embedding_config
    emb_config = await _get_embedding_config(db)

    # 段级混合检索
    from app.services.chat_retrieval import retrieve_relevant_segments
    segs = _get_segments(rec)
    retrieved = await retrieve_relevant_segments(
        question=req.message,
        rec_id=rec_id,
        segments=segs,
        emb_config=emb_config,
        top_k=5,
    )

    # 构建 system prompt
    context_parts = []
    context_parts.append(f"会议标题：{rec.title or rec.original_filename}")
    if rec.summary_text:
        context_parts.append(f"\n会议摘要：\n{rec.summary_text}")
    if retrieved:
        context_parts.append("\n相关原文片段：")
        for r in retrieved:
            ts = f"[{r['start']:.1f}s]" if r['start'] is not None else ""
            sp = f" {r['speaker']}" if r.get('speaker') else ""
            context_parts.append(f"{ts}{sp}: {r['text']}")

    system_prompt = (
        "你是一个会议助手，基于以下会议内容回答用户问题。\n"
        "请基于原文回答，引用来源时说明来自哪段原文。\n"
        "如果不确定，请如实说明，不要编造信息。\n"
        f"\n{chr(10).join(context_parts)}"
    )

    # 构建 messages 列表（含多轮历史）
    messages = [{"role": "system", "content": system_prompt}]
    if req.history:
        for h in req.history:
            role = h.get("role", "user")
            content = h.get("content", "")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": req.message})

    # 调用 LLM（复用 llm 模块的并发控制）
    import httpx

    sem = llm._get_llm_semaphore()
    async with sem:
        try:
            headers = {"Content-Type": "application/json"}
            if model_config.api_key:
                headers["Authorization"] = f"Bearer {model_config.api_key}"
            payload = {
                "model": model_config.model,
                "messages": messages,
                "temperature": 0.3,
                "stream": False,
                "options": {"num_ctx": model_config.max_context_length},
            }
            timeout = httpx.Timeout(settings.llm_timeout, connect=30.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                url = f"{model_config.base_url}/chat/completions"
                logger.info(f"Chat LLM 调用: url={url}, model={model_config.model}, messages={len(messages)}")
                resp = await client.post(url, json=payload, headers=headers)
                resp.raise_for_status()
                data = resp.json()
                reply = data["choices"][0]["message"]["content"]
        except Exception as e:
            logger.error(f"Chat LLM 调用失败: {type(e).__name__}: {e}", exc_info=True)
            return ChatResponse(reply=f"AI 回答失败：{e}")

    # 构建来源
    sources = []
    for r in retrieved:
        sources.append({
            "segment_index": r["segment_index"],
            "text": r["text"],
            "start": r.get("start"),
            "end": r.get("end"),
            "speaker": r.get("speaker"),
        })

    return ChatResponse(reply=reply, sources=sources)


@router.post("/reindex-segments")
async def reindex_all_segments(
    db: AsyncSession = Depends(get_db),
    force: bool = Query(False, description="true=强制重建，false=仅补充缺失"),
):
    """为所有已完成且有转录段的录音重建段级向量索引。"""
    from app.services.embedding import EmbeddingModelConfig
    from app.services.vector_store import vector_store

    # 获取 embedding 模型配置
    result = await db.execute(
        select(LLMModel).where(
            LLMModel.model_type == "embedding",
            LLMModel.is_enabled == True,
        ).order_by(LLMModel.is_default.desc(), LLMModel.sort_order.asc())
    )
    model = result.scalars().first()
    if not model:
        raise HTTPException(status_code=400, detail="未配置 embedding 模型")

    # 获取所有 status=done 且有 transcript_segments 的录音
    recs_result = await db.execute(
        select(Recording).where(
            Recording.status == "done",
            Recording.transcript_segments.isnot(None),
        )
    )
    all_recs = recs_result.scalars().all()

    indexed = 0
    skipped = 0
    errors = 0
    for rec in all_recs:
        segs = _get_segments(rec)
        if not segs:
            skipped += 1
            continue

        # 非强制模式：跳过已有段索引的录音
        if not force:
            existing = vector_store.segment_count(rec.id)
            if existing > 0:
                skipped += 1
                continue

        try:
            await _ensure_segment_embeddings(rec.id)
            indexed += 1
        except Exception as e:
            logger.error(f"段级索引重建失败: rec_id={rec.id}, error={e}")
            errors += 1

    return {"detail": f"重建完成: 索引 {indexed} 条, 跳过 {skipped} 条, 失败 {errors} 条"}
