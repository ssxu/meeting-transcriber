"""MCP (Model Context Protocol) Server — 向支持 MCP 的应用开放录音搜索能力。

实现 MCP Streamable HTTP 传输协议，暴露以下工具：
  - search_recordings: 搜索录音（关键词/语义/混合），返回摘要
  - get_recording_detail: 获取单个录音的详细信息（含完整摘要）

端点: POST /mcp
认证: Bearer token（通过 MCP_TOKEN 环境变量配置）
"""
import logging
import json
from datetime import datetime
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import async_session
from app.config import settings
from app.models import Recording, LLMModel
from app.services.embedding import EmbeddingModelConfig, get_embedding
from app.services.vector_store import vector_store
from app.utils.timezone import format_datetime

logger = logging.getLogger("meeting-transcriber.mcp")

router = APIRouter(prefix="/mcp", tags=["mcp"])

# MCP 协议版本
MCP_PROTOCOL_VERSION = "2024-11-05"
MCP_SERVER_NAME = "meeting-transcriber"
MCP_SERVER_VERSION = "1.0.0"

# ===== MCP 工具定义 =====

TOOLS = [
    {
        "name": "search_recordings",
        "description": (
            "搜索会议录音。支持关键词全文搜索和语义向量搜索。\n"
            "返回匹配录音的标题、摘要、创建时间、相关度分数。\n"
            "适用于：查找特定主题的会议、回顾讨论内容、检索决策记录。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词或自然语言查询",
                },
                "mode": {
                    "type": "string",
                    "enum": ["keyword", "semantic", "hybrid", "auto"],
                    "default": "auto",
                    "description": "搜索模式: keyword(全文) / semantic(语义) / hybrid(混合) / auto(自动选择)",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "返回结果数量上限",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_recording_detail",
        "description": (
            "获取指定录音的详细信息，包括完整摘要、关键词、待办事项、说话人列表。\n"
            "需要提供录音 ID，可通过 search_recordings 获取。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "recording_id": {
                    "type": "integer",
                    "description": "录音 ID",
                },
                "include_transcript": {
                    "type": "boolean",
                    "default": False,
                    "description": "是否包含逐字稿全文（可能很长）",
                },
            },
            "required": ["recording_id"],
        },
    },
    {
        "name": "list_recent_recordings",
        "description": (
            "列出最近的已完成录音，按创建时间倒序排列。\n"
            "返回标题、摘要、创建时间，便于快速浏览最近的会议。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 50,
                    "default": 10,
                    "description": "返回数量上限",
                },
            },
        },
    },
]


# ===== 辅助函数 =====

def _check_auth(request: Request):
    """校验 MCP token。"""
    if not settings.mcp_token:
        return  # 未配置 token，跳过认证（内网部署）
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        token = ""
    if token != settings.mcp_token:
        raise HTTPException(status_code=401, detail="MCP token 无效")


async def _get_embedding_config():
    """获取 embedding 模型配置。"""
    from sqlalchemy import select as sel
    async with async_session() as db:
        result = await db.execute(
            sel(LLMModel).where(
                LLMModel.model_type == "embedding",
                LLMModel.is_default == True,
                LLMModel.is_enabled == True,
            )
        )
        model = result.scalars().first()
        if not model:
            result = await db.execute(
                sel(LLMModel).where(
                    LLMModel.model_type == "embedding",
                    LLMModel.is_enabled == True,
                ).order_by(LLMModel.sort_order.asc())
            )
            model = result.scalars().first()
        if not model:
            return None
        return EmbeddingModelConfig(
            base_url=model.base_url,
            api_key=model.api_key,
            model=model.model_id,
        )


def _format_recording_brief(rec: Recording) -> dict:
    """格式化录音摘要（列表用）。"""
    return {
        "id": rec.id,
        "title": rec.title or rec.original_filename,
        "summary": (rec.summary_text or "")[:500],
        "created_at": format_datetime(rec.created_at, "%Y-%m-%d %H:%M") if rec.created_at else "",
        "duration_sec": rec.duration,
        "keywords": rec.keywords or [],
        "tags": rec.tags or [],
    }


def _format_recording_detail(rec: Recording, include_transcript: bool = False) -> dict:
    """格式化录音详情。"""
    data = {
        "id": rec.id,
        "title": rec.title or rec.original_filename,
        "summary": rec.summary_text or "",
        "notes": rec.notes or "",
        "created_at": format_datetime(rec.created_at, "%Y-%m-%d %H:%M") if rec.created_at else "",
        "duration_sec": rec.duration,
        "keywords": rec.keywords or [],
        "tags": rec.tags or [],
        "action_items": rec.action_items or [],
        "engine": rec.engine,
        "speakers": [],
    }
    # 提取说话人
    if rec.transcript_segments:
        speakers = set()
        for seg in rec.transcript_segments:
            sp = seg.get("speaker")
            if sp:
                speakers.add(sp)
        data["speakers"] = sorted(speakers)
    # 可选包含逐字稿
    if include_transcript:
        data["transcript"] = rec.transcript_text or ""
    return data


# ===== 工具执行 =====

async def _exec_search_recordings(args: dict) -> str:
    """执行录音搜索。"""
    query = args.get("query", "").strip()
    if not query:
        return json.dumps({"error": "query 不能为空"}, ensure_ascii=False)

    mode = args.get("mode", "auto")
    limit = min(args.get("limit", 10), settings.mcp_max_results, 50)

    async with async_session() as db:
        # 获取 embedding 配置
        emb_config = None
        if mode in ("auto", "semantic", "hybrid"):
            emb_config = await _get_embedding_config()

        if mode == "auto":
            mode = "hybrid" if emb_config else "keyword"
        if mode in ("semantic", "hybrid") and not emb_config:
            mode = "keyword"

        # 基础查询：已完成的录音
        base_query = select(Recording).where(
            Recording.status == "done",
            Recording.transcript_text.isnot(None),
        )

        # 全文搜索
        keyword_hits: dict[int, Recording] = {}
        if mode in ("keyword", "hybrid"):
            escaped_q = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped_q}%"
            kw_query = base_query.where(
                or_(
                    Recording.transcript_text.like(pattern, escape="\\"),
                    Recording.summary_text.like(pattern, escape="\\"),
                    Recording.title.like(pattern, escape="\\"),
                    Recording.notes.like(pattern, escape="\\"),
                )
            )
            result = await db.execute(kw_query)
            for r in result.scalars().all():
                keyword_hits[r.id] = r

        # 语义搜索
        semantic_hits: dict[int, tuple[Recording, float]] = {}
        if mode in ("semantic", "hybrid"):
            if emb_config and vector_store.is_ready:
                try:
                    query_vec = await get_embedding(query, emb_config)
                    # 获取有效录音 ID 列表
                    filter_result = await db.execute(base_query.with_only_columns(Recording.id))
                    valid_rec_ids = [row[0] for row in filter_result.all()]
                    if valid_rec_ids and query_vec:
                        id_list = ",".join(str(i) for i in valid_rec_ids)
                        zvec_filter = f"recording_id IN ({id_list})"
                        zvec_results = vector_store.search(
                            query_vector=query_vec,
                            topk=min(len(valid_rec_ids), 100),
                            filter=zvec_filter,
                        )
                        rec_ids = [r["recording_id"] for r in zvec_results if r["recording_id"]]
                        if rec_ids:
                            rec_result = await db.execute(
                                select(Recording).where(Recording.id.in_(rec_ids))
                            )
                            rec_map = {r.id: r for r in rec_result.scalars().all()}
                            for zr in zvec_results:
                                rid = zr["recording_id"]
                                if rid and rid in rec_map:
                                    semantic_hits[rid] = (rec_map[rid], zr["score"])
                except Exception as e:
                    logger.error(f"MCP 语义搜索失败: {e}")
                    if mode == "semantic":
                        return json.dumps({"error": f"语义搜索失败: {e}"}, ensure_ascii=False)
                    mode = "keyword"

        # 合并结果
        all_rec_ids = set(keyword_hits.keys()) | set(semantic_hits.keys())
        if not all_rec_ids:
            return json.dumps({
                "total": 0,
                "mode": mode,
                "items": [],
                "message": "未找到匹配的录音",
            }, ensure_ascii=False)

        results = []
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

            brief = _format_recording_brief(rec)
            brief["score"] = round(score, 4)
            results.append(brief)

        results.sort(key=lambda x: (x["score"], x["created_at"]), reverse=True)
        results = results[:limit]

        return json.dumps({
            "total": len(results),
            "mode": mode,
            "items": results,
        }, ensure_ascii=False, indent=2)


async def _exec_get_recording_detail(args: dict) -> str:
    """获取录音详情。"""
    rec_id = args.get("recording_id")
    if not rec_id:
        return json.dumps({"error": "recording_id 不能为空"}, ensure_ascii=False)
    include_transcript = args.get("include_transcript", False)

    async with async_session() as db:
        rec = await db.get(Recording, rec_id)
        if not rec:
            return json.dumps({"error": f"录音 {rec_id} 不存在"}, ensure_ascii=False)
        if rec.status != "done":
            return json.dumps({"error": f"录音 {rec_id} 状态为 {rec.status}，尚未完成处理"}, ensure_ascii=False)

        data = _format_recording_detail(rec, include_transcript)
        return json.dumps(data, ensure_ascii=False, indent=2)


async def _exec_list_recent_recordings(args: dict) -> str:
    """列出最近录音。"""
    limit = min(args.get("limit", 10), settings.mcp_max_results, 50)

    async with async_session() as db:
        result = await db.execute(
            select(Recording)
            .where(Recording.status == "done", Recording.transcript_text.isnot(None))
            .order_by(Recording.created_at.desc())
            .limit(limit)
        )
        recs = result.scalars().all()

        items = [_format_recording_brief(r) for r in recs]
        return json.dumps({
            "total": len(items),
            "items": items,
        }, ensure_ascii=False, indent=2)


TOOL_EXECUTORS = {
    "search_recordings": _exec_search_recordings,
    "get_recording_detail": _exec_get_recording_detail,
    "list_recent_recordings": _exec_list_recent_recordings,
}


# ===== MCP 协议处理 =====

@router.post("")
async def mcp_handler(request: Request):
    """MCP Streamable HTTP 端点。

    接受 JSON-RPC 2.0 请求，支持以下方法:
    - initialize: 初始化 MCP 会话
    - tools/list: 列出可用工具
    - tools/call: 调用工具
    """
    _check_auth(request)

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 支持批量请求（JSON-RPC 2.0），但 MCP 通常单请求
    if isinstance(body, list):
        responses = [await _handle_single_request(req) for req in body]
        return JSONResponse(responses)

    return JSONResponse(await _handle_single_request(body))


async def _handle_single_request(req: dict) -> dict:
    """处理单个 JSON-RPC 请求。"""
    req_id = req.get("id")
    method = req.get("method", "")

    # ===== initialize =====
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {
                    "tools": {},
                },
                "serverInfo": {
                    "name": MCP_SERVER_NAME,
                    "version": MCP_SERVER_VERSION,
                },
            },
        }

    # ===== notifications/initialized（客户端确认初始化完成）=====
    if method == "notifications/initialized":
        # 通知无需返回 result
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }

    # ===== tools/list =====
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS,
            },
        }

    # ===== tools/call =====
    if method == "tools/call":
        params = req.get("params", {})
        tool_name = params.get("name", "")
        tool_args = params.get("arguments", {})

        if tool_name not in TOOL_EXECUTORS:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({"error": f"未知工具: {tool_name}"}, ensure_ascii=False),
                    }],
                    "isError": True,
                },
            }

        try:
            result_text = await TOOL_EXECUTORS[tool_name](tool_args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": result_text,
                    }],
                    "isError": False,
                },
            }
        except Exception as e:
            logger.error(f"MCP 工具执行失败: tool={tool_name}, error={e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{
                        "type": "text",
                        "text": json.dumps({"error": str(e)}, ensure_ascii=False),
                    }],
                    "isError": True,
                },
            }

    # ===== ping =====
    if method == "ping":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {},
        }

    # ===== 未知方法 =====
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    }


@router.get("")
async def mcp_info():
    """MCP 端点信息（GET 用于健康检查/发现）。"""
    return {
        "server": MCP_SERVER_NAME,
        "version": MCP_SERVER_VERSION,
        "protocol": MCP_PROTOCOL_VERSION,
        "tools": [t["name"] for t in TOOLS],
        "auth_required": bool(settings.mcp_token),
    }
