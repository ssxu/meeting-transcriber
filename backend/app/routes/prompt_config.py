"""提示词配置管理路由模块 - 管理会议纪要 Map/Reduce 提示词模板。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import PromptConfig
from app.schemas import PromptConfigOut, PromptConfigUpdate
from app.config import (
    settings,
    DEFAULT_MAP_PROMPT,
    DEFAULT_REDUCE_PROMPT,
    DEFAULT_SINGLE_EXTRACT_PROMPT,
)

logger = logging.getLogger("meeting-transcriber.prompt_config")

router = APIRouter(prefix="/api/prompt-configs", tags=["prompt-configs"])

# 提示词类型与默认值的映射
DEFAULT_PROMPTS = {
    "map": DEFAULT_MAP_PROMPT,
    "reduce": DEFAULT_REDUCE_PROMPT,
    "single_extract": DEFAULT_SINGLE_EXTRACT_PROMPT,
}

PROMPT_DESCRIPTIONS = {
    "map": "Map 阶段提示词 - 用于逐块提取会议逐字稿片段的结构化信息（议题、决议、待办、摘要、关键词）",
    "reduce": "Reduce 阶段提示词 - 将多个片段摘要整合为完整的会议纪要文档",
    "single_extract": "单次提取提示词 - 会议逐字稿较短时直接提取全部信息",
}


async def _ensure_default_configs(db: AsyncSession):
    """确保数据库中存在所有提示词类型的记录（首次初始化）。"""
    result = await db.execute(select(PromptConfig))
    existing_types = {r.prompt_type for r in result.scalars().all()}

    for ptype, default_content in DEFAULT_PROMPTS.items():
        if ptype not in existing_types:
            config = PromptConfig(
                prompt_type=ptype,
                content=default_content,
                description=PROMPT_DESCRIPTIONS.get(ptype, ""),
                is_enabled=True,
            )
            db.add(config)
            logger.info(f"初始化默认提示词配置: {ptype}")

    if db.new:
        await db.commit()


@router.get("", response_model=list[PromptConfigOut])
async def list_prompt_configs(db: AsyncSession = Depends(get_db)):
    """列出所有提示词配置。自动初始化缺失的默认配置。"""
    await _ensure_default_configs(db)
    result = await db.execute(select(PromptConfig).order_by(
        # 按 map -> reduce -> single_extract 排序
        PromptConfig.prompt_type.asc()
    ))
    return [PromptConfigOut.model_validate(r) for r in result.scalars().all()]


@router.get("/{prompt_type}", response_model=PromptConfigOut)
async def get_prompt_config(prompt_type: str, db: AsyncSession = Depends(get_db)):
    """获取指定类型的提示词配置。"""
    if prompt_type not in DEFAULT_PROMPTS:
        raise HTTPException(status_code=400, detail=f"不支持的提示词类型: {prompt_type}，可选: map, reduce, single_extract")

    await _ensure_default_configs(db)
    result = await db.execute(select(PromptConfig).where(PromptConfig.prompt_type == prompt_type))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="提示词配置不存在")
    return PromptConfigOut.model_validate(config)


@router.patch("/{prompt_type}", response_model=PromptConfigOut)
async def update_prompt_config(prompt_type: str, req: PromptConfigUpdate, db: AsyncSession = Depends(get_db)):
    """更新指定类型的提示词配置。"""
    if prompt_type not in DEFAULT_PROMPTS:
        raise HTTPException(status_code=400, detail=f"不支持的提示词类型: {prompt_type}，可选: map, reduce, single_extract")

    await _ensure_default_configs(db)
    result = await db.execute(select(PromptConfig).where(PromptConfig.prompt_type == prompt_type))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="提示词配置不存在")

    if req.content is not None:
        config.content = req.content
    if req.description is not None:
        config.description = req.description
    if req.is_enabled is not None:
        config.is_enabled = req.is_enabled

    await db.commit()
    await db.refresh(config)
    logger.info(f"提示词配置已更新: type={prompt_type}, enabled={config.is_enabled}")
    return PromptConfigOut.model_validate(config)


@router.post("/{prompt_type}/reset", response_model=PromptConfigOut)
async def reset_prompt_config(prompt_type: str, db: AsyncSession = Depends(get_db)):
    """重置指定类型的提示词为默认值。"""
    if prompt_type not in DEFAULT_PROMPTS:
        raise HTTPException(status_code=400, detail=f"不支持的提示词类型: {prompt_type}，可选: map, reduce, single_extract")

    await _ensure_default_configs(db)
    result = await db.execute(select(PromptConfig).where(PromptConfig.prompt_type == prompt_type))
    config = result.scalars().first()
    if not config:
        raise HTTPException(status_code=404, detail="提示词配置不存在")

    config.content = DEFAULT_PROMPTS[prompt_type]
    config.is_enabled = True

    await db.commit()
    await db.refresh(config)
    logger.info(f"提示词配置已重置为默认值: type={prompt_type}")
    return PromptConfigOut.model_validate(config)


@router.get("/defaults/{prompt_type}")
async def get_default_prompt(prompt_type: str):
    """获取指定类型的默认提示词（不读取数据库，直接返回 config.py 中的默认值）。"""
    if prompt_type not in DEFAULT_PROMPTS:
        raise HTTPException(status_code=400, detail=f"不支持的提示词类型: {prompt_type}，可选: map, reduce, single_extract")
    return {
        "prompt_type": prompt_type,
        "content": DEFAULT_PROMPTS[prompt_type],
        "description": PROMPT_DESCRIPTIONS.get(prompt_type, ""),
    }


# ===== 工具函数：供 llm.py / recordings.py 调用 =====

async def get_active_prompts(db: AsyncSession) -> dict[str, str | None]:
    """获取当前生效的自定义提示词配置。

    返回: {"map": str|None, "reduce": str|None, "single_extract": str|None}
    只返回数据库中已启用(is_enabled=True)且内容非空的自定义配置。
    未配置或被禁用的 key 值为 None，由调用方回退到场景模板。
    """
    await _ensure_default_configs(db)
    result = await db.execute(select(PromptConfig))
    configs = {r.prompt_type: r for r in result.scalars().all()}

    prompts = {}
    for ptype, default_content in DEFAULT_PROMPTS.items():
        config = configs.get(ptype)
        if config and config.is_enabled and config.content.strip():
            # 如果内容和默认值完全相同，视为未自定义
            if config.content.strip() == default_content.strip():
                prompts[ptype] = None
            else:
                prompts[ptype] = config.content
        else:
            prompts[ptype] = None

    return prompts
