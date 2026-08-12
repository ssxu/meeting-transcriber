FROM docker.m.daocloud.io/python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

# .env 文件可选：通过 docker-compose environment 或运行时挂载
# 如需在构建时包含 .env，请确保文件存在
EXPOSE 8000
# 应用启动时 init_db() 会自动执行 alembic upgrade head
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
