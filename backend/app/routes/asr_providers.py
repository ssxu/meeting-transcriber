"""ASR 提供商管理路由模块 - 管理 OpenAI 兼容的语音识别服务配置。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import AsrProvider
from app.schemas import AsrProviderCreate, AsrProviderUpdate, AsrProviderOut

logger = logging.getLogger("meeting-transcriber.asr_providers")

router = APIRouter(prefix="/api/asr-providers", tags=["asr_providers"])


@router.get("", response_model=list[AsrProviderOut])
async def list_asr_providers(db: AsyncSession = Depends(get_db)):
    """列出所有 ASR 提供商配置。"""
    result = await db.execute(
        select(AsrProvider).order_by(AsrProvider.sort_order.asc(), AsrProvider.id.asc())
    )
    return [AsrProviderOut.from_model(r) for r in result.scalars().all()]


@router.get("/enabled", response_model=list[AsrProviderOut])
async def list_enabled_asr_providers(db: AsyncSession = Depends(get_db)):
    """列出所有启用的 ASR 提供商（前端选择下拉用）。"""
    result = await db.execute(
        select(AsrProvider)
        .where(AsrProvider.is_enabled == True)
        .order_by(AsrProvider.sort_order.asc(), AsrProvider.id.asc())
    )
    return [AsrProviderOut.from_model(r) for r in result.scalars().all()]


@router.post("", response_model=AsrProviderOut)
async def create_asr_provider(req: AsrProviderCreate, db: AsyncSession = Depends(get_db)):
    """创建 ASR 提供商配置。"""
    if req.is_default:
        result = await db.execute(
            select(AsrProvider).where(AsrProvider.is_default == True)
        )
        for p in result.scalars().all():
            p.is_default = False
    provider = AsrProvider(
        name=req.name,
        base_url=req.base_url,
        auth_header=req.auth_header,
        auth_value=req.auth_value,
        timeout=req.timeout,
        supports_speaker=req.supports_speaker,
        supports_hotwords=req.supports_hotwords,
        is_default=req.is_default,
        is_enabled=req.is_enabled,
        sort_order=req.sort_order,
        description=req.description,
    )
    db.add(provider)
    await db.commit()
    await db.refresh(provider)
    logger.info(f"ASR 提供商已创建: id={provider.id}, name={provider.name}")
    return AsrProviderOut.from_model(provider)


@router.get("/{provider_id}", response_model=AsrProviderOut)
async def get_asr_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    """获取 ASR 提供商配置详情。"""
    provider = await db.get(AsrProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="ASR 提供商不存在")
    return AsrProviderOut.from_model(provider)


@router.patch("/{provider_id}", response_model=AsrProviderOut)
async def update_asr_provider(provider_id: int, req: AsrProviderUpdate, db: AsyncSession = Depends(get_db)):
    """更新 ASR 提供商配置。"""
    provider = await db.get(AsrProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="ASR 提供商不存在")
    if req.name is not None:
        provider.name = req.name
    if req.base_url is not None:
        provider.base_url = req.base_url
    if req.auth_header is not None:
        provider.auth_header = req.auth_header
    if req.auth_value is not None:
        if "****" not in req.auth_value:
            provider.auth_value = req.auth_value
    if req.timeout is not None:
        provider.timeout = req.timeout
    if req.supports_speaker is not None:
        provider.supports_speaker = req.supports_speaker
    if req.supports_hotwords is not None:
        provider.supports_hotwords = req.supports_hotwords
    if req.is_enabled is not None:
        provider.is_enabled = req.is_enabled
    if req.sort_order is not None:
        provider.sort_order = req.sort_order
    if req.description is not None:
        provider.description = req.description
    if req.is_default is not None:
        if req.is_default:
            result = await db.execute(
                select(AsrProvider).where(AsrProvider.is_default == True)
            )
            for other in result.scalars().all():
                if other.id != provider_id:
                    other.is_default = False
        provider.is_default = req.is_default
    await db.commit()
    await db.refresh(provider)
    logger.info(f"ASR 提供商已更新: id={provider_id}")
    return AsrProviderOut.from_model(provider)


@router.delete("/{provider_id}")
async def delete_asr_provider(provider_id: int, db: AsyncSession = Depends(get_db)):
    """删除 ASR 提供商配置。"""
    provider = await db.get(AsrProvider, provider_id)
    if not provider:
        raise HTTPException(status_code=404, detail="ASR 提供商不存在")
    await db.delete(provider)
    await db.commit()
    logger.info(f"ASR 提供商已删除: id={provider_id}")
    return {"detail": "已删除"}


async def resolve_asr_provider(
    provider_id: int | None, db: AsyncSession
) -> AsrProvider | None:
    """解析 ASR 提供商配置。

    优先级:
    1. provider_id 不为 None → 使用指定的提供商
    2. provider_id 为 None → 查找数据库中 is_default=True 的提供商
    3. 无默认提供商 → 查找第一个启用的提供商
    4. 无启用的提供商 → 返回 None（回退到 settings.asr_base_url）
    """
    if provider_id is not None:
        provider = await db.get(AsrProvider, provider_id)
        if not provider:
            raise HTTPException(status_code=404, detail=f"ASR 提供商 {provider_id} 不存在")
        if not provider.is_enabled:
            raise HTTPException(status_code=400, detail=f"ASR 提供商 {provider.name} 已禁用")
        return provider

    # 查找默认提供商
    result = await db.execute(
        select(AsrProvider).where(
            AsrProvider.is_default == True,
            AsrProvider.is_enabled == True,
        )
    )
    default = result.scalars().first()
    if default:
        logger.info(f"使用默认 ASR 提供商: {default.name}")
        return default

    # 查找第一个启用的提供商
    result = await db.execute(
        select(AsrProvider)
        .where(AsrProvider.is_enabled == True)
        .order_by(AsrProvider.sort_order.asc(), AsrProvider.id.asc())
    )
    first_enabled = result.scalars().first()
    if first_enabled:
        logger.info(f"使用首个启用的 ASR 提供商: {first_enabled.name}")
        return first_enabled

    # 无可用提供商，回退到全局配置
    logger.info("未找到启用的 ASR 提供商，回退到环境变量配置")
    return None