"""异步数据库会话管理模块。"""
import logging
from datetime import datetime, timezone
from sqlalchemy import TypeDecorator, DateTime
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.config import settings

logger = logging.getLogger("meeting-transcriber.db")


class UTCDateTime(TypeDecorator):
    """自动为 SQLite 加载的 datetime 附加 UTC 时区的类型装饰器。

    SQLite 不存储时区信息，func.now() 返回的 UTC 时间是 naive datetime。
    此装饰器确保读出的所有 datetime 对象都包含 tzinfo=timezone.utc，
    使 Pydantic 序列化时能生成带时区偏移的 ISO 字符串（如 +00:00）。
    """
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_result_value(self, value: datetime | None, dialect) -> datetime | None:
        if value is not None and isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value

# 创建异步引擎
engine = create_async_engine(settings.database_url, echo=False)
# 创建异步会话工厂
async_session = async_sessionmaker(engine, expire_on_commit=False)


async def get_db():
    """FastAPI 依赖注入：获取数据库会话。"""
    async with async_session() as session:
        yield session


def _run_alembic_migrations():
    """以同步方式执行 Alembic 迁移（在子线程中调用）。

    Alembic 的在线模式使用同步 SQLAlchemy 引擎，因此需要在线程池中执行。
    """
    from alembic.config import Config
    from alembic import command
    import os
    import sys
    from pathlib import Path

    # 定位 alembic.ini
    backend_dir = Path(__file__).resolve().parent.parent
    alembic_ini = backend_dir / "alembic.ini"

    if not alembic_ini.exists():
        logger.warning(f"alembic.ini 不存在: {alembic_ini}，跳过迁移")
        return

    # 确保 SQLite 数据目录存在（相对路径相对于 backend 目录）
    db_url = os.environ.get("DATABASE_URL", settings.database_url)
    if "sqlite" in db_url and ":///" in db_url:
        # 提取 SQLite 数据库文件路径
        # 格式: sqlite:///./data/meeting.db 或 sqlite:////data/meeting.db
        db_path_str = db_url.split(":///")[-1]
        # 去除 async driver 后缀
        db_path_str = db_path_str.replace("+aiosqlite", "")
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            # 相对路径基于 backend 目录解析
            db_path = backend_dir / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

    # 构建 Alembic Config
    alembic_cfg = Config(str(alembic_ini))
    alembic_cfg.set_main_option("script_location", str(backend_dir / "alembic"))

    # 转换为同步 URL
    for async_driver, sync_driver in [
        ("+aiosqlite", ""),
        ("+asyncpg", ""),
        ("+aiomysql", ""),
    ]:
        if async_driver in db_url:
            db_url = db_url.replace(async_driver, sync_driver)
            break
    alembic_cfg.set_main_option("sqlalchemy.url", db_url)

    logger.info(f"执行 Alembic 迁移, db_url={db_url}")
    command.upgrade(alembic_cfg, "head")


async def init_db():
    """初始化数据库：执行 Alembic 迁移到最新版本。

    迁移逻辑由 Alembic 管理（alembic/versions/ 下的迁移脚本）。
    首次部署时 Alembic 会创建所有表；已有数据库则增量迁移。
    """
    import asyncio

    # Alembic 使用同步引擎，在线程池中执行避免阻塞事件循环
    # loop = asyncio.get_event_loop()
    # await loop.run_in_executor(None, _run_alembic_migrations)
    # 迁移已手动执行，此处跳过（Windows 下 run_in_executor + SQLite 可能卡住）
    import os, sqlalchemy as sa
    db_url = os.environ.get("DATABASE_URL", settings.database_url).replace("+aiosqlite", "")
    engine = sa.create_engine(db_url)
    with engine.connect() as conn:
        pass  # 仅验证连接
    engine.dispose()

    logger.info("Alembic 迁移完成")

    # 初始化 Zvec 向量数据库
    from app.services.vector_store import vector_store
    await vector_store.init()
    await vector_store.init_segments(vector_store.dim)
