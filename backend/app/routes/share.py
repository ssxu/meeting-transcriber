"""分享链接路由模块 - 只读分享访问。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import Recording
from app.schemas import RecordingDetail

logger = logging.getLogger("meeting-transcriber.share")

router = APIRouter(prefix="/api/share", tags=["share"])


@router.get("/{token}", response_model=RecordingDetail)
async def get_shared_recording(token: str, db: AsyncSession = Depends(get_db)):
    """通过分享 token 获取只读录音详情。"""
    result = await db.execute(
        select(Recording).where(
            Recording.share_token == token,
            Recording.is_shared == True,
        )
    )
    rec = result.scalars().first()
    if not rec:
        raise HTTPException(status_code=404, detail="分享链接无效或已失效")
    data = RecordingDetail.model_validate(rec)
    # 脱敏：不向匿名用户暴露 share_token
    data.share_token = None
    return data
