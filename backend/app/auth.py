"""认证模块 - JWT 令牌签发与校验。"""
import jwt
import logging
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Depends, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from app.config import settings

logger = logging.getLogger("meeting-transcriber.auth")

security = HTTPBearer(auto_error=False)

# 不需要认证的路径前缀
PUBLIC_PATHS = (
    "/api/auth/login",
    "/api/health",
    "/api/share/",  # 分享链接公开访问
    "/mcp",  # MCP 端点使用独立 token 认证
)

# 不需要认证的路径精确匹配（带 query 参数判断）
PUBLIC_PATHS_WITH_TOKEN = (
    "/api/recordings/audio-share",  # 分享页音频访问
)

# 分享页面不需要认证（前端路由）
PUBLIC_FRONTEND_PATHS = (
    "/share/",
)


def create_token() -> str:
    """签发 JWT 令牌。"""
    expire = datetime.now(timezone.utc) + timedelta(hours=settings.jwt_expire_hours)
    payload = {
        "exp": expire,
        "iat": datetime.now(timezone.utc),
        "sub": "meeting-transcriber",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def verify_token(token: str) -> bool:
    """校验 JWT 令牌。"""
    try:
        jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        return True
    except jwt.ExpiredSignatureError:
        return False
    except jwt.InvalidTokenError:
        return False


async def auth_middleware(request: Request, call_next):
    """认证中间件 - 拦截需要认证的请求。"""
    # CORS 预检请求直接放行，避免阻断跨域
    if request.method == "OPTIONS":
        return await call_next(request)
    path = request.url.path

    # 公开路径直接放行
    for pub in PUBLIC_PATHS:
        if path.startswith(pub):
            return await call_next(request)

    # 带 share_token 的音频访问放行（路径前缀匹配）
    for pub_wt in PUBLIC_PATHS_WITH_TOKEN:
        if path.startswith(pub_wt):
            return await call_next(request)

    # 非 /api 路径放行（前端静态资源由 nginx 处理）
    if not path.startswith("/api/"):
        return await call_next(request)

    # 从 Authorization header 提取 token
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header[7:]
    else:
        # 也支持 query parameter（用于 audio/video 等直接嵌入的 URL）
        token = request.query_params.get("token")

    if not token:
        return _unauthorized_response("未提供认证令牌")

    if not verify_token(token):
        return _unauthorized_response("认证令牌无效或已过期")

    return await call_next(request)


def _unauthorized_response(detail: str):
    """返回 401 响应。"""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=401,
        content={"detail": detail},
    )


# ===== 登录路由 =====
router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """密码登录，返回 JWT 令牌。"""
    if req.password != settings.auth_password:
        raise HTTPException(status_code=401, detail="密码错误")
    token = create_token()
    logger.info("用户登录成功")
    return LoginResponse(token=token, expires_in=settings.jwt_expire_hours * 3600)


@router.get("/check")
async def check_auth():
    """检查认证状态（需要携带有效 token 才能访问）。"""
    return {"authenticated": True}
