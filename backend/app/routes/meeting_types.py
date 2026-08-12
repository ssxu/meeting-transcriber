"""会议类型管理路由模块 - 管理录音类型及对应的 LLM 提示词模板（Map/Reduce/Single-Extract）。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import MeetingType
from app.schemas import MeetingTypeCreate, MeetingTypeUpdate, MeetingTypeOut

logger = logging.getLogger("meeting-transcriber.meeting_types")

router = APIRouter(prefix="/api/meeting-types", tags=["meeting-types"])


@router.get("", response_model=list[MeetingTypeOut])
async def list_meeting_types(db: AsyncSession = Depends(get_db)):
    """列出所有会议类型。内置类型排前面，自定义类型按创建时间倒序。"""
    result = await db.execute(
        select(MeetingType).order_by(
            MeetingType.is_builtin.desc(),
            MeetingType.created_at.desc(),
        )
    )
    return [MeetingTypeOut.model_validate(r) for r in result.scalars().all()]


@router.post("", response_model=MeetingTypeOut)
async def create_meeting_type(req: MeetingTypeCreate, db: AsyncSession = Depends(get_db)):
    """创建会议类型。"""
    if req.is_default:
        result = await db.execute(select(MeetingType).where(MeetingType.is_default == True))
        for mt in result.scalars().all():
            mt.is_default = False
    mt = MeetingType(
        name=req.name,
        description=req.description,
        category=req.category,
        sub_type=req.sub_type,
        map_prompt=req.map_prompt,
        reduce_prompt=req.reduce_prompt,
        single_extract_prompt=req.single_extract_prompt,
        is_default=req.is_default,
        is_builtin=False,
    )
    db.add(mt)
    await db.commit()
    await db.refresh(mt)
    logger.info(f"会议类型已创建: id={mt.id}, name={mt.name}, category={mt.category}, sub_type={mt.sub_type}")
    return MeetingTypeOut.model_validate(mt)


@router.get("/{mt_id}", response_model=MeetingTypeOut)
async def get_meeting_type(mt_id: int, db: AsyncSession = Depends(get_db)):
    """获取会议类型详情。"""
    mt = await db.get(MeetingType, mt_id)
    if not mt:
        raise HTTPException(status_code=404, detail="会议类型不存在")
    return MeetingTypeOut.model_validate(mt)


@router.patch("/{mt_id}", response_model=MeetingTypeOut)
async def update_meeting_type(mt_id: int, req: MeetingTypeUpdate, db: AsyncSession = Depends(get_db)):
    """更新会议类型。内置类型可以编辑 prompt 但不能删除。"""
    mt = await db.get(MeetingType, mt_id)
    if not mt:
        raise HTTPException(status_code=404, detail="会议类型不存在")
    if req.name is not None:
        mt.name = req.name
    if req.description is not None:
        mt.description = req.description
    if req.category is not None:
        mt.category = req.category
    if req.sub_type is not None:
        mt.sub_type = req.sub_type
    if req.map_prompt is not None:
        mt.map_prompt = req.map_prompt
    if req.reduce_prompt is not None:
        mt.reduce_prompt = req.reduce_prompt
    if req.single_extract_prompt is not None:
        mt.single_extract_prompt = req.single_extract_prompt
    if req.is_default is not None:
        if req.is_default:
            result = await db.execute(select(MeetingType).where(MeetingType.is_default == True))
            for other in result.scalars().all():
                if other.id != mt_id:
                    other.is_default = False
        mt.is_default = req.is_default
    await db.commit()
    await db.refresh(mt)
    return MeetingTypeOut.model_validate(mt)


@router.delete("/{mt_id}")
async def delete_meeting_type(mt_id: int, db: AsyncSession = Depends(get_db)):
    """删除会议类型。内置类型不可删除。"""
    mt = await db.get(MeetingType, mt_id)
    if not mt:
        raise HTTPException(status_code=404, detail="会议类型不存在")
    if mt.is_builtin:
        raise HTTPException(status_code=400, detail="内置类型不可删除，可直接编辑其提示词模板")
    await db.delete(mt)
    await db.commit()
    return {"detail": "已删除"}


@router.post("/reset/{mt_id}", response_model=MeetingTypeOut)
async def reset_builtin_prompts(mt_id: int, db: AsyncSession = Depends(get_db)):
    """重置内置类型的 prompt 模板为代码中的场景默认值。"""
    mt = await db.get(MeetingType, mt_id)
    if not mt:
        raise HTTPException(status_code=404, detail="会议类型不存在")
    if not mt.is_builtin:
        raise HTTPException(status_code=400, detail="仅内置类型可重置")
    from app.services.llm import _build_map_prompt_for_scene, _build_reduce_prompt_for_scene, _build_single_extract_prompt_for_scene
    mt.map_prompt = _build_map_prompt_for_scene(mt.category, mt.sub_type)
    mt.reduce_prompt = _build_reduce_prompt_for_scene(mt.category, mt.sub_type)
    mt.single_extract_prompt = _build_single_extract_prompt_for_scene(mt.category, mt.sub_type)
    await db.commit()
    await db.refresh(mt)
    logger.info(f"内置类型 prompt 已重置: id={mt_id}, sub_type={mt.sub_type}")
    return MeetingTypeOut.model_validate(mt)


@router.get("/scene-types/list")
async def list_scene_types():
    """返回所有可用的内容场景大类和细分类型（用于前端选择器）。"""
    from app.services.llm import CONTENT_CATEGORIES, SUB_TYPES
    categories = [{"key": k, "label": v["label"]} for k, v in CONTENT_CATEGORIES.items()]
    sub_types = [
        {
            "key": k,
            "label": v["label"],
            "category": v["category"],
            "map_focus": v["map_focus"],
            "reduce_output": v["reduce_output"],
        }
        for k, v in SUB_TYPES.items()
    ]
    return {"categories": categories, "sub_types": sub_types}
