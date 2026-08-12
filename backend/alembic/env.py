"""Alembic 迁移环境配置。

支持两种数据库 URL：
  - 同步（Alembic 迁移用）：从 DATABASE_URL 环境变量转换，或回退到默认 SQLite
  - 异步（应用运行时用）：config.py 中的 settings.database_url

Alembic 迁移使用同步 SQLAlchemy 引擎，需要将 async driver (aiosqlite)
转换为同步 driver (sqlite3)。
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool
from alembic import context

# 确保 app 包在 path 中
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.models import Base

# Alembic 配置对象
config = context.config

# 日志配置
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 目标 metadata — 所有模型自动纳入
target_metadata = Base.metadata


def _get_sync_url() -> str:
    """将异步 database_url 转换为同步 URL 供 Alembic 使用。

    sqlite+aiosqlite:// → sqlite://
    postgresql+asyncpg:// → postgresql://
    mysql+aiomysql:// → mysql://
    """
    import os
    from pathlib import Path

    url = os.environ.get("DATABASE_URL", settings.database_url)
    # 优先使用环境变量中的同步 URL（Docker 环境可能直接提供同步 URL）
    # 如果是异步 URL，做转换
    replacements = {
        "+aiosqlite": "",
        "+asyncpg": "",
        "+aiomysql": "",
    }
    for async_driver, sync_driver in replacements.items():
        if async_driver in url:
            url = url.replace(async_driver, sync_driver)
            break

    # 确保 SQLite 数据目录存在（相对路径相对于 backend 目录）
    if "sqlite" in url and ":///" in url:
        db_path_str = url.split(":///")[-1]
        db_path = Path(db_path_str)
        if not db_path.is_absolute():
            backend_dir = Path(__file__).resolve().parents[1]
            db_path = backend_dir / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)

    return url


def run_migrations_offline() -> None:
    """离线模式：生成 SQL 脚本而不连接数据库。"""
    url = _get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,  # SQLite 需要 batch 模式支持 ALTER
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：连接数据库执行迁移。"""
    # 覆盖 alembic.ini 中的 sqlalchemy.url
    config.set_main_option("sqlalchemy.url", _get_sync_url())

    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,  # SQLite 需要 batch 模式支持 ALTER TABLE
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
