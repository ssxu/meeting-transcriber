"""热词库管理路由模块。"""
import logging
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import HotwordLibrary, Hotword
from app.schemas import (
    HotwordLibraryCreate, HotwordLibraryUpdate, HotwordLibraryOut, HotwordLibraryDetail,
    HotwordCreate, HotwordOut, HotwordBatchAdd, HotwordImportText,
)

logger = logging.getLogger("meeting-transcriber.hotwords")

router = APIRouter(prefix="/api/hotwords", tags=["hotwords"])


@router.get("/libraries", response_model=list[HotwordLibraryOut])
async def list_libraries(db: AsyncSession = Depends(get_db)):
    """列出所有热词库。"""
    result = await db.execute(
        select(
            HotwordLibrary,
            func.count(Hotword.id).label("hotword_count"),
        )
        .outerjoin(Hotword, HotwordLibrary.id == Hotword.library_id)
        .group_by(HotwordLibrary.id)
        .order_by(HotwordLibrary.created_at.desc())
    )
    rows = result.all()
    out = []
    for lib, count in rows:
        item = HotwordLibraryOut.model_validate(lib)
        item.hotword_count = count
        out.append(item)
    return out


@router.post("/libraries", response_model=HotwordLibraryOut)
async def create_library(req: HotwordLibraryCreate, db: AsyncSession = Depends(get_db)):
    """创建热词库。"""
    if req.is_default:
        # 取消其他默认
        result = await db.execute(select(HotwordLibrary).where(HotwordLibrary.is_default == True))
        for lib in result.scalars().all():
            lib.is_default = False
    lib = HotwordLibrary(name=req.name, description=req.description, is_default=req.is_default)
    db.add(lib)
    await db.commit()
    await db.refresh(lib)
    item = HotwordLibraryOut.model_validate(lib)
    item.hotword_count = 0
    return item


@router.get("/libraries/{lib_id}", response_model=HotwordLibraryDetail)
async def get_library(lib_id: int, db: AsyncSession = Depends(get_db)):
    """获取热词库详情（含热词列表）。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    result = await db.execute(
        select(Hotword).where(Hotword.library_id == lib_id).order_by(Hotword.created_at.asc())
    )
    hotwords = result.scalars().all()
    item = HotwordLibraryDetail.model_validate(lib)
    item.hotword_count = len(hotwords)
    item.hotwords = [HotwordOut.model_validate(h) for h in hotwords]
    return item


@router.patch("/libraries/{lib_id}", response_model=HotwordLibraryOut)
async def update_library(lib_id: int, req: HotwordLibraryUpdate, db: AsyncSession = Depends(get_db)):
    """更新热词库。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    if req.name is not None:
        lib.name = req.name
    if req.description is not None:
        lib.description = req.description
    if req.is_default is not None:
        if req.is_default:
            result = await db.execute(select(HotwordLibrary).where(HotwordLibrary.is_default == True))
            for other in result.scalars().all():
                if other.id != lib_id:
                    other.is_default = False
        lib.is_default = req.is_default
    await db.commit()
    await db.refresh(lib)
    item = HotwordLibraryOut.model_validate(lib)
    count_result = await db.execute(select(func.count(Hotword.id)).where(Hotword.library_id == lib_id))
    item.hotword_count = count_result.scalar() or 0
    return item


@router.delete("/libraries/{lib_id}")
async def delete_library(lib_id: int, db: AsyncSession = Depends(get_db)):
    """删除热词库及其所有热词。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    await db.execute(delete(Hotword).where(Hotword.library_id == lib_id))
    await db.delete(lib)
    await db.commit()
    return {"detail": "已删除"}


# ===== 热词条目管理 =====

@router.post("/libraries/{lib_id}/words", response_model=list[HotwordOut])
async def add_hotwords(lib_id: int, req: HotwordBatchAdd, db: AsyncSession = Depends(get_db)):
    """批量添加热词到热词库。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    created = []
    for hw in req.hotwords:
        item = Hotword(library_id=lib_id, word=hw.word, weight=hw.weight)
        db.add(item)
        created.append(item)
    await db.commit()
    for item in created:
        await db.refresh(item)
    return [HotwordOut.model_validate(h) for h in created]


@router.post("/libraries/{lib_id}/import", response_model=list[HotwordOut])
async def import_hotwords_text(lib_id: int, req: HotwordImportText, db: AsyncSession = Depends(get_db)):
    """从文本批量导入热词，每行一个词，格式: 词 权重(可选，默认1)。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    created = []
    for line in req.text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        word = parts[0]
        weight = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
        if weight < 1:
            weight = 1
        if weight > 100:
            weight = 100
        item = Hotword(library_id=lib_id, word=word, weight=weight)
        db.add(item)
        created.append(item)
    await db.commit()
    for item in created:
        await db.refresh(item)
    logger.info(f"导入热词到库{lib_id}: {len(created)}个")
    return [HotwordOut.model_validate(h) for h in created]


@router.delete("/words/{word_id}")
async def delete_hotword(word_id: int, db: AsyncSession = Depends(get_db)):
    """删除单个热词。"""
    hw = await db.get(Hotword, word_id)
    if not hw:
        raise HTTPException(status_code=404, detail="热词不存在")
    await db.delete(hw)
    await db.commit()
    return {"detail": "已删除"}


@router.get("/libraries/{lib_id}/vocabulary")
async def build_vocabulary(lib_id: int, db: AsyncSession = Depends(get_db)):
    """构建ASR热词库字符串，用于传递给转录接口的vocabulary_id参数。"""
    lib = await db.get(HotwordLibrary, lib_id)
    if not lib:
        raise HTTPException(status_code=404, detail="热词库不存在")
    result = await db.execute(select(Hotword).where(Hotword.library_id == lib_id))
    hotwords = result.scalars().all()
    if not hotwords:
        return {"vocabulary_id": "", "count": 0}
    # ASR 格式: "词汇1 权重1 词汇2 权重2"
    parts = []
    for hw in hotwords:
        parts.append(f"{hw.word} {hw.weight}")
    vocab = " ".join(parts)
    return {"vocabulary_id": vocab, "count": len(hotwords)}
