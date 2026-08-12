# Alembic 数据库迁移指南

## 概述

本项目使用 [Alembic](https://alembic.sqlalchemy.org/) 管理数据库 schema 迁移。
应用启动时（`init_db()`）会自动执行 `alembic upgrade head`，将数据库迁移到最新版本。

## 目录结构

```
backend/
├── alembic.ini          # Alembic 配置
├── alembic/
│   ├── env.py           # 迁移环境配置（从 app.models 导入 Base.metadata）
│   ├── script.py.mako   # 迁移脚本模板
│   └── versions/        # 迁移版本文件（自动生成）
│       └── 565d72bc9b59_initial_schema.py
```

## 常用命令

> 以下命令在 `backend/` 目录下执行。

### 生成新迁移

当修改了 `app/models.py` 中的模型后，生成迁移脚本：

```bash
# 设置环境变量（与 docker-compose 一致）
export DATABASE_URL="sqlite+aiosqlite:///./data/meeting.db"

# 自动生成迁移
python -m alembic revision --autogenerate -m "描述本次变更"
```

### 执行迁移

```bash
# 迁移到最新版本
python -m alembic upgrade head

# 迁移到指定版本
python -m alembic upgrade 565d72bc9b59

# 回滚一个版本
python -m alembic downgrade -1
```

### 查看状态

```bash
# 当前版本
python -m alembic current

# 迁移历史
python -m alembic history --verbose
```

## 已有数据库升级

如果数据库在引入 Alembic 之前已存在（schema 与当前模型一致），
需要标记当前数据库为最新版本，**不实际执行迁移**：

```bash
python -m alembic stamp head
```

这会在 `alembic_version` 表中记录当前版本号，后续 `alembic upgrade head` 不会重复执行已应用的迁移。

## Docker 环境

Dockerfile 已将 `alembic/` 和 `alembic.ini` 打入镜像。
应用启动时 `init_db()` 自动执行迁移，无需额外步骤。

`docker-compose.yml` 中的 `DATABASE_URL` 环境变量同时供应用和 Alembic 使用。
Alembic 的 `env.py` 会自动将异步 driver（如 `+aiosqlite`）转换为同步 driver。

## 注意事项

1. **SQLite 限制**：SQLite 不支持部分 ALTER TABLE 操作。Alembic 的 `render_as_batch=True` 
   已启用（在 `env.py` 中），会自动使用 "copy-table" 策略处理。
2. **迁移文件提交到 Git**：`alembic/versions/` 下的迁移文件必须提交到版本控制。
3. **不要手动修改迁移文件**：已生成的迁移文件不应修改，如需调整请创建新迁移。
4. **生成前检查**：`alembic revision --autogenerate` 后请检查生成的文件，确保检测到的变更正确。
