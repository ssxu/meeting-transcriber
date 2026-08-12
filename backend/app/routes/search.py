"""统一搜索路由 - 全文搜索 + 语义向量搜索 + 混合搜索，支持多维筛选和排序。

向量检索使用 Zvec 嵌入式向量数据库（HNSW 索引，毫秒级搜索）。
"""
import logging
import re
from datetime import datetime, timezone
from fastapi import APIRouter, HTTPException, Depends, Query
from typing import Optional
from sqlalchemy import select, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.db import get_db
from app.models import Recording, LLMModel
from app.services.embedding import EmbeddingModelConfig, get_embedding
from app.services.rerank import RerankModelConfig, rerank
from app.services.vector_store import vector_store

logger = logging.getLogger("meeting-transcriber.search")

router = APIRouter(prefix="/api/search", tags=["search"])


# ===== 辅助函数 =====

def _highlight(text: str, keywords: list[str]) -> str:
    """在文本中高亮关键词，返回 HTML。"""
    if not text or not keywords:
        return text or ""
    result = text
    for kw in keywords:
        if not kw.strip():
            continue
        pattern = re.compile(re.escape(kw), re.IGNORECASE)
        result = pattern.sub(lambda m: f'<mark>{m.group()}</mark>', result)
    return result


def _make_snippet(source: str, query: str, max_len: int = 200) -> str:
    """从源文本中提取包含查询词的片段并高亮。"""
    if not source:
        return ""
    idx = source.lower().find(query.lower())
    if idx >= 0:
        start = max(0, idx - 60)
        end = min(len(source), idx + len(query) + 140)
        snippet = ("..." if start > 0 else "") + source[start:end] + ("..." if end < len(source) else "")
    else:
        snippet = source[:max_len] + ("..." if len(source) > max_len else "")
    return _highlight(snippet, [query])


def _extract_speakers(segments) -> list[str]:
    """从 transcript_segments 提取说话人列表。"""
    if not segments:
        return []
    speakers = set()
    for seg in segments:
        sp = seg.get("speaker")
        if sp:
            speakers.add(sp)
    return sorted(speakers)


async def _get_embedding_config(db: AsyncSession) -> EmbeddingModelConfig | None:
    """获取默认 embedding 模型配置。"""
    result = await db.execute(
        select(LLMModel).where(
            LLMModel.model_type == "embedding",
            LLMModel.is_default == True,
            LLMModel.is_enabled == True,
        )
    )
    model = result.scalars().first()
    if not model:
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.model_type == "embedding",
                LLMModel.is_enabled == True,
            ).order_by(LLMModel.sort_order.asc())
        )
        model = result.scalars().first()
    if not model:
        return None
    return EmbeddingModelConfig(base_url=model.base_url, api_key=model.api_key, model=model.model_id)


async def _get_rerank_config(db: AsyncSession) -> RerankModelConfig | None:
    """获取默认 rerank 模型配置。"""
    result = await db.execute(
        select(LLMModel).where(
            LLMModel.model_type == "rerank",
            LLMModel.is_default == True,
            LLMModel.is_enabled == True,
        )
    )
    model = result.scalars().first()
    if not model:
        result = await db.execute(
            select(LLMModel).where(
                LLMModel.model_type == "rerank",
                LLMModel.is_enabled == True,
            ).order_by(LLMModel.sort_order.asc())
        )
        model = result.scalars().first()
    if not model:
        return None
    return RerankModelConfig(base_url=model.base_url, api_key=model.api_key, model=model.model_id)


def _build_zvec_filter(speaker: str, date_from: str, date_to: str, tag: str,
                       valid_rec_ids: list[int] | None = None) -> str | None:
    """构建 Zvec 标量过滤条件。

    Zvec filter 语法类似 SQL WHERE，支持 AND/OR。
    注意：Zvec 只能过滤存储在 collection 中的标量字段，
    speaker/tag 等复杂字段仍需在 SQL 侧预筛 recording_id 列表。
    """
    conditions = []
    if valid_rec_ids is not None:
        if not valid_rec_ids:
            return "recording_id == -1"  # 永假
        id_list = ",".join(str(i) for i in valid_rec_ids)
        conditions.append(f"recording_id IN ({id_list})")

    if date_from.strip():
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
            conditions.append(f"created_at_ts >= {dt_from}")
        except ValueError:
            pass
    if date_to.strip():
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc).timestamp()
            conditions.append(f"created_at_ts <= {dt_to}")
        except ValueError:
            pass

    if not conditions:
        return None
    return " AND ".join(conditions)


# ===== 搜索 API =====

@router.get("")
async def unified_search(
    q: str = Query("", description="搜索关键词"),
    mode: str = Query("auto", description="搜索模式: keyword(全文) / semantic(语义) / hybrid(混合) / auto(自动)"),
    speaker: str = Query("", description="说话人筛选"),
    date_from: str = Query("", description="起始日期 YYYY-MM-DD"),
    date_to: str = Query("", description="截止日期 YYYY-MM-DD"),
    tag: str = Query("", description="标签筛选"),
    sort_by: str = Query("relevance", description="排序: relevance(相关度) / time(时间)"),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    """统一搜索接口。

    - mode=keyword: 仅全文搜索（SQL LIKE）
    - mode=semantic: 仅语义搜索（Zvec HNSW 向量检索）
    - mode=hybrid: 同时执行全文+语义，合并结果
    - mode=auto: 有 embedding 模型时用 hybrid，否则降级为 keyword
    """
    q = q.strip()
    if not q:
        return {"items": [], "total": 0, "mode": "empty"}

    emb_config = await _get_embedding_config(db)

    if mode == "auto":
        mode = "hybrid" if emb_config else "keyword"
    if mode in ("semantic", "hybrid") and not emb_config:
        mode = "keyword"

    # ===== 第一步：SQL 侧预筛（说话人、标签）获取有效 recording_id =====
    base_query = select(Recording).where(Recording.status == "done")

    if speaker.strip():
        escaped_speaker = speaker.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        base_query = base_query.where(
            Recording.transcript_segments.like(f'%"speaker": "{escaped_speaker}"%', escape='\\')
        )
    if date_from.strip():
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            base_query = base_query.where(Recording.created_at >= dt_from)
        except ValueError:
            pass
    if date_to.strip():
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            base_query = base_query.where(Recording.created_at <= dt_to.replace(hour=23, minute=59, second=59))
        except ValueError:
            pass
    if tag.strip():
        escaped_tag = tag.strip().replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        base_query = base_query.where(Recording.tags.like(f'%"{escaped_tag}"%', escape='\\'))

    # 全文搜索
    keyword_hits: dict[int, Recording] = {}
    if mode in ("keyword", "hybrid"):
        escaped_q = q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        pattern = f"%{escaped_q}%"
        kw_query = base_query.where(
            or_(
                Recording.transcript_text.like(pattern, escape='\\'),
                Recording.summary_text.like(pattern, escape='\\'),
                Recording.title.like(pattern, escape='\\'),
                Recording.original_filename.like(pattern, escape='\\'),
                Recording.notes.like(pattern, escape='\\'),
            )
        )
        result = await db.execute(kw_query)
        for r in result.scalars().all():
            keyword_hits[r.id] = r

    # 语义搜索（Zvec）
    semantic_hits: dict[int, tuple[Recording, float]] = {}
    if mode in ("semantic", "hybrid"):
        # 获取符合筛选条件的 recording_id 列表（用于 Zvec filter）
        filter_result = await db.execute(base_query.with_only_columns(Recording.id))
        valid_rec_ids = [row[0] for row in filter_result.all()]

        if valid_rec_ids and emb_config and vector_store.is_ready:
            try:
                query_vec = await get_embedding(q, emb_config)
            except Exception as e:
                logger.error(f"生成查询向量失败: {e}")
                if mode == "semantic":
                    return {"items": [], "total": 0, "mode": "semantic_error", "error": str(e)}
                mode = "keyword"
                query_vec = None
            else:
                if query_vec:
                    # 构建 Zvec 过滤条件
                    zvec_filter = _build_zvec_filter(
                        speaker, date_from, date_to, tag, valid_rec_ids
                    )
                    # Zvec 向量搜索
                    try:
                        zvec_results = vector_store.search(
                            query_vector=query_vec,
                            topk=min(len(valid_rec_ids), 100),
                            filter=zvec_filter,
                        )
                    except Exception as e:
                        logger.error(f"Zvec 搜索失败: {e}")
                        zvec_results = []

                    # 加载 Recording 数据
                    rec_ids_from_zvec = [r["recording_id"] for r in zvec_results if r["recording_id"]]
                    if rec_ids_from_zvec:
                        rec_result = await db.execute(
                            select(Recording).where(Recording.id.in_(rec_ids_from_zvec))
                        )
                        rec_map = {r.id: r for r in rec_result.scalars().all()}

                        for zr in zvec_results:
                            rid = zr["recording_id"]
                            if rid and rid in rec_map:
                                semantic_hits[rid] = (rec_map[rid], zr["score"])

    # ===== 合并结果 =====
    all_rec_ids = set(keyword_hits.keys()) | set(semantic_hits.keys())
    if not all_rec_ids:
        return {"items": [], "total": 0, "mode": mode}

    results: list[dict] = []
    for rid in all_rec_ids:
        rec = keyword_hits.get(rid) or semantic_hits.get(rid, (None,))[0]
        if not rec:
            continue

        kw_score = 1.0 if rid in keyword_hits else 0.0
        sem_score = semantic_hits.get(rid, (None, 0.0))[1] if rid in semantic_hits else 0.0

        if mode == "hybrid":
            score = sem_score * 0.7 + kw_score * 0.3
        elif mode == "semantic":
            score = sem_score
        else:
            score = kw_score

        source_text = rec.transcript_text or rec.summary_text or ""
        snippet = _make_snippet(source_text, q)

        results.append({
            "id": rec.id,
            "title": rec.title or rec.original_filename,
            "original_filename": rec.original_filename,
            "status": rec.status,
            "snippet": snippet,
            "created_at": rec.created_at.isoformat() if rec.created_at else None,
            "score": round(score, 4),
            "tags": rec.tags or [],
            "speakers": _extract_speakers(rec.transcript_segments),
            "has_transcript": bool(rec.transcript_text),
        })

    # ===== 可选：rerank 重排序 =====
    if mode in ("semantic", "hybrid") and len(results) > 1:
        rerank_config = await _get_rerank_config(db)
        if rerank_config:
            try:
                documents = [r["snippet"] for r in results]
                rerank_results = await rerank(q, documents, rerank_config, top_n=len(results))
                for rank_idx, (orig_idx, r_score) in enumerate(rerank_results):
                    results[orig_idx]["score"] = round(r_score, 4)
                # rerank 更新了 score，但不覆盖用户选择的 sort_by
                # 用户选 time 就按 time 排，选 relevance 就按新的 score 排
            except Exception as e:
                logger.warning(f"Rerank 失败，使用原始分数: {e}")

    # ===== 排序 =====
    if sort_by == "time":
        results.sort(key=lambda x: x["created_at"] or "", reverse=True)
    else:
        results.sort(key=lambda x: (x["score"], x["created_at"] or ""), reverse=True)

    total = len(results)
    results = results[:limit]

    return {"items": results, "total": total, "mode": mode}


# ===== Embedding 索引管理 =====

@router.post("/reindex")
async def reindex_embeddings(
    db: AsyncSession = Depends(get_db),
    force: bool = Query(
        False,
        description="true=强制全量重建（覆盖已有索引）；false=仅补充缺失索引",
    ),
):
    """为所有已完成但缺少 embedding 的录音生成向量索引（写入 Zvec）。

    force=False（默认，增量）：跳过 Zvec 中已存在的记录，只补充缺失项。
    force=True（全量）：删除 Zvec 中不在当前录音列表里的孤立文档，然后对所有录音执行 upsert。
    """
    emb_config = await _get_embedding_config(db)
    if not emb_config:
        raise HTTPException(status_code=400, detail="未配置 embedding 模型，请先在模型管理中添加 embedding 类型的模型")

    if not vector_store.is_ready:
        raise HTTPException(status_code=500, detail="Zvec 向量数据库未初始化")

    # 获取所有已完成且有转录文本的录音
    result = await db.execute(
        select(Recording).where(
            Recording.status == "done",
            Recording.transcript_text.isnot(None),
        )
    )
    all_recs = result.scalars().all()

    indexed_count = 0
    errors = 0
    skip_count = 0

    for rec in all_recs:
        doc_id = f"rec_{rec.id}"

        # 增量模式：跳过已存在的记录
        if not force:
            try:
                existing = vector_store.collection.fetch(ids=doc_id)
                if existing:
                    skip_count += 1
                    continue
            except Exception:
                pass  # 不存在或出错，继续插入

        try:
            emb_text = f"{rec.title or ''}\n{rec.summary_text or ''}\n{rec.notes or ''}\n{rec.transcript_text or ''}"[:8000]
            vec = await get_embedding(emb_text, emb_config)

            # 检查维度是否匹配，不匹配则重建 collection
            await vector_store.ensure_dim(len(vec))

            created_ts = rec.created_at.timestamp() if rec.created_at else 0.0
            vector_store.insert(
                doc_id=doc_id,
                recording_id=rec.id,
                embedding=vec,
                title=rec.title or rec.original_filename or "",
                created_at_ts=created_ts,
            )
            indexed_count += 1
        except Exception as e:
            logger.error(f"生成 embedding 失败: recording_id={rec.id}, error={e}")
            errors += 1

    # 优化索引
    vector_store.optimize()
    vector_store.flush()

    if force:
        logger.info(f"Zvec 全量索引完成: 写入 {indexed_count} 条, 失败 {errors} 条")
        return {
            "detail": f"全量索引完成: 写入 {indexed_count} 条, 失败 {errors} 条",
        }
    else:
        logger.info(f"Zvec 增量索引完成: 新增 {indexed_count} 条, 跳过 {skip_count} 条, 失败 {errors} 条")
        return {
            "detail": f"增量索引完成: 新增 {indexed_count} 条, 跳过 {skip_count} 条, 失败 {errors} 条",
        }


@router.post("/reindex/{rec_id}")
async def reindex_single(rec_id: int, db: AsyncSession = Depends(get_db)):
    """为单条录音重新生成 embedding 并写入 Zvec。"""
    emb_config = await _get_embedding_config(db)
    if not emb_config:
        raise HTTPException(status_code=400, detail="未配置 embedding 模型")

    rec = await db.get(Recording, rec_id)
    if not rec:
        raise HTTPException(status_code=404, detail="录音不存在")
    if not rec.transcript_text:
        raise HTTPException(status_code=400, detail="录音无转录文本")

    # 生成 embedding
    emb_text = f"{rec.title or ''}\n{rec.summary_text or ''}\n{rec.notes or ''}\n{rec.transcript_text or ''}"[:8000]
    vec = await get_embedding(emb_text, emb_config)

    # 确保维度匹配
    await vector_store.ensure_dim(len(vec))

    # upsert（存在则覆盖）
    doc_id = f"rec_{rec.id}"
    created_ts = rec.created_at.timestamp() if rec.created_at else 0.0
    vector_store.insert(
        doc_id=doc_id,
        recording_id=rec.id,
        embedding=vec,
        title=rec.title or rec.original_filename or "",
        created_at_ts=created_ts,
    )
    vector_store.flush()

    return {"detail": "向量已更新", "dim": len(vec)}


@router.get("/status")
async def search_status(db: AsyncSession = Depends(get_db)):
    """搜索系统状态：embedding 模型配置、Zvec 索引覆盖率。"""
    emb_config = await _get_embedding_config(db)
    rerank_config = await _get_rerank_config(db)

    # 统计总录音数
    total_recs = await db.execute(
        select(func.count()).select_from(
            select(Recording).where(Recording.status == "done", Recording.transcript_text.isnot(None)).subquery()
        )
    )
    total = total_recs.scalar() or 0

    # Zvec 索引数
    indexed_count = vector_store.count() if vector_store.is_ready else 0

    return {
        "embedding_model": emb_config.model if emb_config else None,
        "rerank_model": rerank_config.model if rerank_config else None,
        "total_recordings": total,
        "indexed_recordings": indexed_count,
        "coverage": f"{indexed_count}/{total}" if total > 0 else "0/0",
        "semantic_ready": emb_config is not None and vector_store.is_ready,
        "vector_store": "zvec" if vector_store.is_ready else "not_initialized",
    }
