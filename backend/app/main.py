"""FastAPI 应用入口模块。"""
import os
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from app.db import init_db
from app.routes import recordings, stats, share, voiceprints, hotwords, meeting_types, models, search
from app.routes.transcription import router as transcription_router, schedule_router, start_queue_poller
from app.routes.mcp import router as mcp_router
from app.routes.prompt_config import router as prompt_config_router
from app.auth import auth_middleware, router as auth_router
from app.config import settings
from app.services.vector_store import vector_store

# ===== 日志系统配置 =====
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("meeting-transcriber")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时创建存储目录和初始化数据库。"""
    for subdir in ["recordings", "transcripts", "summaries"]:
        os.makedirs(os.path.join(settings.storage_path, subdir), exist_ok=True)
    logger.info(f"存储目录已就绪: {settings.storage_path}")
    await init_db()
    logger.info("数据库初始化完成")
    # init_db() 内部已初始化 Zvec 向量数据库，此处仅打印状态
    if vector_store.is_ready:
        logger.info(f"Zvec 向量数据库已就绪: dim={vector_store.dim}")
    else:
        logger.warning("Zvec 向量数据库未就绪，语义搜索将降级为全文搜索")

    # 启动转录队列定时轮询
    start_queue_poller()

    yield
    # 关闭时 flush 向量数据库
    try:
        vector_store.flush()
    except Exception:
        pass
    logger.info("应用关闭")


app = FastAPI(title="Meeting Transcriber", version="1.1.0", lifespan=lifespan)

# ===== CORS 中间件 =====
cors_origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册认证路由（公开）
app.include_router(auth_router)


# ===== 全局异常处理中间件 =====
@app.middleware("http")
async def global_exception_handler(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as e:
        logger.error(f"未捕获异常: {e}", exc_info=True)
        logger.error(f"请求路径: {request.method} {request.url}")
        logger.error(f"请求头: {dict(request.headers)}")
        logger.error(f"请求参数: {dict(request.query_params)}")
        try:
            body = await request.body()
            if body:
                logger.error(f"请求体: {body[:2000].decode('utf-8', errors='replace')}")
        except Exception:
            pass
        return JSONResponse(
            status_code=500,
            content={"detail": f"服务器内部错误: {str(e)}"},
        )


# 注册业务路由
app.include_router(recordings.router)
app.include_router(stats.router)
app.include_router(share.router)
app.include_router(voiceprints.router)
app.include_router(hotwords.router)
app.include_router(meeting_types.router)
app.include_router(models.router)
app.include_router(search.router)
app.include_router(transcription_router)
app.include_router(schedule_router)
app.include_router(mcp_router)
app.include_router(prompt_config_router)

# ===== 认证中间件（放在最后，确保所有路由已注册）=====
@app.middleware("http")
async def register_auth_middleware(request: Request, call_next):
    return await auth_middleware(request, call_next)


@app.get("/api/health")
async def health():
    """健康检查端点。"""
    return {"status": "ok", "version": "1.1.0"}


@app.get("/api/config")
async def config():
    """返回系统配置（前端需要）。"""
    return {"timezone": settings.timezone}
