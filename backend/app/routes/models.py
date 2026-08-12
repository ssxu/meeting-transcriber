"""LLM 模型管理路由模块 - 管理可调用的大语言模型配置。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import LLMModel
from app.schemas import LLMModelCreate, LLMModelUpdate, LLMModelOut

logger = logging.getLogger("meeting-transcriber.models")

router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("", response_model=list[LLMModelOut])
async def list_models(db: AsyncSession = Depends(get_db)):
    """列出所有 LLM 模型配置。"""
    result = await db.execute(
        select(LLMModel).order_by(LLMModel.sort_order.asc(), LLMModel.id.asc())
    )
    return [LLMModelOut.from_model(r) for r in result.scalars().all()]


@router.get("/enabled", response_model=list[LLMModelOut])
async def list_enabled_models(db: AsyncSession = Depends(get_db)):
    """列出所有启用的模型（前端选择下拉用）。"""
    result = await db.execute(
        select(LLMModel)
        .where(LLMModel.is_enabled == True)
        .order_by(LLMModel.sort_order.asc(), LLMModel.id.asc())
    )
    return [LLMModelOut.from_model(r) for r in result.scalars().all()]


@router.post("", response_model=LLMModelOut)
async def create_model(req: LLMModelCreate, db: AsyncSession = Depends(get_db)):
    """创建模型配置。"""
    if req.is_default:
        result = await db.execute(
            select(LLMModel).where(LLMModel.is_default == True, LLMModel.model_type == req.model_type)
        )
        for m in result.scalars().all():
            m.is_default = False
    model = LLMModel(
        name=req.name,
        model_id=req.model_id,
        base_url=req.base_url,
        api_key=req.api_key,
        model_type=req.model_type,
        is_default=req.is_default,
        is_enabled=req.is_enabled,
        sort_order=req.sort_order,
        max_context_length=req.max_context_length,
        description=req.description,
    )
    db.add(model)
    await db.commit()
    await db.refresh(model)
    logger.info(f"模型已创建: id={model.id}, name={model.name}, model_id={model.model_id}, type={model.model_type}")
    return LLMModelOut.from_model(model)


@router.get("/{model_id}", response_model=LLMModelOut)
async def get_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """获取模型配置详情。"""
    model = await db.get(LLMModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    return LLMModelOut.from_model(model)


@router.patch("/{model_id}", response_model=LLMModelOut)
async def update_model(model_id: int, req: LLMModelUpdate, db: AsyncSession = Depends(get_db)):
    """更新模型配置。"""
    model = await db.get(LLMModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    if req.name is not None:
        model.name = req.name
    if req.model_id is not None:
        model.model_id = req.model_id
    if req.base_url is not None:
        model.base_url = req.base_url
    if req.api_key is not None:
        # 跳过脱敏后的 api_key（包含 **** 表示是前端回显的掩码值）
        if "****" not in req.api_key:
            model.api_key = req.api_key
    if req.is_enabled is not None:
        model.is_enabled = req.is_enabled
    if req.sort_order is not None:
        model.sort_order = req.sort_order
    if req.description is not None:
        model.description = req.description
    if req.max_context_length is not None:
        model.max_context_length = req.max_context_length
    if req.model_type is not None:
        model.model_type = req.model_type
    if req.is_default is not None:
        if req.is_default:
            result = await db.execute(
                select(LLMModel).where(
                    LLMModel.is_default == True,
                    LLMModel.model_type == model.model_type,
                )
            )
            for other in result.scalars().all():
                if other.id != model_id:
                    other.is_default = False
        model.is_default = req.is_default
    await db.commit()
    await db.refresh(model)
    logger.info(f"模型已更新: id={model_id}")
    return LLMModelOut.from_model(model)



@router.delete("/{model_id}")
async def delete_model(model_id: int, db: AsyncSession = Depends(get_db)):
    """删除模型配置。"""
    model = await db.get(LLMModel, model_id)
    if not model:
        raise HTTPException(status_code=404, detail="模型不存在")
    was_default = model.is_default
    await db.delete(model)
    await db.commit()
    logger.info(f"模型已删除: id={model_id}")
    return {"detail": "已删除"}


async def get_model_config_by_id(model_id: int | None, db: AsyncSession):
    """根据模型 ID 获取 ModelConfig。
    
    优先级:
    1. model_id 不为 None → 使用指定模型
    2. model_id 为 None → 查找数据库中 is_default=True 的模型
    3. 数据库无默认模型 → 返回 None（回退到全局 settings 配置）
    """
    from app.services.llm import ModelConfig
    if model_id is not None:
        model = await db.get(LLMModel, model_id)
        if not model:
            raise HTTPException(status_code=404, detail=f"模型 {model_id} 不存在")
        if not model.is_enabled:
            raise HTTPException(status_code=400, detail=f"模型 {model.name} 已禁用")
        return ModelConfig(
            base_url=model.base_url,
            api_key=model.api_key,
            model=model.model_id,
            max_context_length=model.max_context_length,
        )
    # model_id 为 None，查找数据库中的默认 chat 模型
    # 必须按 model_type='chat' 过滤，避免返回 embedding/rerank 类型的默认模型
    result = await db.execute(
        select(LLMModel).where(
            LLMModel.is_default == True,
            LLMModel.is_enabled == True,
            LLMModel.model_type == "chat",
        )
    )
    default_model = result.scalars().first()
    if default_model:
        logger.info(f"使用数据库默认模型: {default_model.name} ({default_model.model_id})")
        return ModelConfig(
            base_url=default_model.base_url,
            api_key=default_model.api_key,
            model=default_model.model_id,
            max_context_length=default_model.max_context_length,
        )
    # 数据库无默认模型，返回 None 让 llm.py 回退到全局 settings
    logger.info("未找到数据库默认模型，回退到全局配置")
    return None
