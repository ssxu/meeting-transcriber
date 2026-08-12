"""声纹管理路由模块 - 管理说话人声纹，与ASR后端同步。"""
import logging
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import VoiceprintSpeaker
from app.schemas import VoiceprintSpeakerOut, VoiceprintSpeakerUpdate
from app.services import asr

logger = logging.getLogger("meeting-transcriber.voiceprints")

router = APIRouter(prefix="/api/voiceprints", tags=["voiceprints"])


@router.get("", response_model=list[VoiceprintSpeakerOut])
async def list_voiceprints(db: AsyncSession = Depends(get_db)):
    """列出所有声纹说话人（本地数据库）。"""
    result = await db.execute(select(VoiceprintSpeaker).order_by(VoiceprintSpeaker.created_at.desc()))
    return [VoiceprintSpeakerOut.model_validate(r) for r in result.scalars().all()]


@router.get("/sync")
async def sync_voiceprints(db: AsyncSession = Depends(get_db)):
    """从ASR后端同步声纹说话人列表到本地数据库。"""
    try:
        remote_speakers = await asr.list_voiceprint_speakers()
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ASR后端同步失败: {e}")

    added, updated, total = 0, 0, 0
    for rs in remote_speakers:
        sid = rs["speaker_id"]
        if not sid:
            continue
        total += 1
        result = await db.execute(select(VoiceprintSpeaker).where(VoiceprintSpeaker.speaker_id == sid))
        local = result.scalars().first()
        if local:
            local.display_name = rs.get("display_name", local.display_name)
            local.description = rs.get("description")
            local.voiceprint_count = rs.get("voiceprint_count", 0)
            updated += 1
        else:
            sp = VoiceprintSpeaker(
                speaker_id=sid,
                display_name=rs.get("display_name", ""),
                description=rs.get("description"),
                voiceprint_count=rs.get("voiceprint_count", 0),
            )
            db.add(sp)
            added += 1
    await db.commit()
    return {"detail": f"同步完成: 新增{added}, 更新{updated}, 总计{total}"}


@router.post("", response_model=VoiceprintSpeakerOut)
async def create_voiceprint(
    display_name: str = Form(..., max_length=200),
    description: str | None = Form(None),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """创建声纹说话人，上传到ASR后端并记录到本地数据库。"""
    file_data = await file.read()
    try:
        result = await asr.create_voiceprint_speaker(
            display_name=display_name,
            file_data=file_data,
            filename=file.filename or "sample.wav",
            content_type=file.content_type or "audio/wav",
            description=description,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ASR后端创建失败: {e}")

    speaker_id = result.get("speaker_id", "")
    if not speaker_id:
        raise HTTPException(status_code=502, detail="ASR后端未返回speaker_id")

    sp = VoiceprintSpeaker(
        speaker_id=speaker_id,
        display_name=result.get("display_name", display_name),
        description=description,
        voiceprint_count=result.get("voiceprint_count", 1),
    )
    db.add(sp)
    await db.commit()
    await db.refresh(sp)
    logger.info(f"声纹说话人已创建: id={sp.id}, speaker_id={speaker_id}")
    return VoiceprintSpeakerOut.model_validate(sp)


@router.post("/{sp_id}/samples")
async def add_samples(
    sp_id: int,
    files: list[UploadFile] = File(...),
    db: AsyncSession = Depends(get_db),
):
    """向已有说话人添加声纹样本。"""
    sp = await db.get(VoiceprintSpeaker, sp_id)
    if not sp:
        raise HTTPException(status_code=404, detail="说话人不存在")

    files_data = []
    for f in files:
        data = await f.read()
        files_data.append((data, f.filename or "sample.wav", f.content_type or "audio/wav"))

    try:
        result = await asr.add_voiceprint_samples(sp.speaker_id, files_data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"ASR后端添加样本失败: {e}")

    sp.voiceprint_count = result.get("voiceprint_count", sp.voiceprint_count)
    await db.commit()
    return {"detail": f"已添加{len(files)}个样本", "voiceprint_count": sp.voiceprint_count}


@router.patch("/{sp_id}", response_model=VoiceprintSpeakerOut)
async def update_voiceprint(
    sp_id: int,
    req: VoiceprintSpeakerUpdate,
    db: AsyncSession = Depends(get_db),
):
    """更新说话人信息（仅本地）。"""
    sp = await db.get(VoiceprintSpeaker, sp_id)
    if not sp:
        raise HTTPException(status_code=404, detail="说话人不存在")
    if req.display_name is not None:
        sp.display_name = req.display_name
    if req.description is not None:
        sp.description = req.description
    await db.commit()
    await db.refresh(sp)
    return VoiceprintSpeakerOut.model_validate(sp)


@router.delete("/{sp_id}")
async def delete_voiceprint(sp_id: int, db: AsyncSession = Depends(get_db)):
    """删除说话人，同时从ASR后端和本地数据库删除。"""
    sp = await db.get(VoiceprintSpeaker, sp_id)
    if not sp:
        raise HTTPException(status_code=404, detail="说话人不存在")
    try:
        await asr.delete_voiceprint_speaker(sp.speaker_id)
    except Exception as e:
        logger.warning(f"ASR后端删除失败(继续本地删除): {e}")
    await db.delete(sp)
    await db.commit()
    return {"detail": "已删除"}
