"""FastAPI entry point for the local rollcall web control panel."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hmac
import os
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .. import __version__
from .service import WebService


WEB_DIR = Path(__file__).resolve().parent
STATIC_DIR = WEB_DIR / "static"


class LoginBody(BaseModel):
    username: str = Field(min_length=1, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class MonitorBody(BaseModel):
    auto_submit: bool = False


class SettingsBody(BaseModel):
    threshold: int = Field(ge=1, le=100)
    auto_submit: bool | None = None


class LocationBindingBody(BaseModel):
    course_id: str = Field(min_length=1, max_length=100)
    course_title: str = Field(default="", max_length=300)
    preset_id: str = Field(min_length=1, max_length=100)


def create_app(service: WebService | None = None) -> FastAPI:
    manager = service or WebService()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await asyncio.to_thread(manager.stop_monitor)

    app = FastAPI(
        title="XMU 畅课签到 Web 控制台",
        version=__version__,
        docs_url="/api/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.service = manager

    @app.middleware("http")
    async def local_basic_auth(request: Request, call_next):
        password = os.environ.get("XMU_ROLLCALL_WEB_PASSWORD", "")
        if password:
            supplied = ""
            authorization = request.headers.get("authorization", "")
            if authorization.lower().startswith("basic "):
                try:
                    decoded = base64.b64decode(authorization.split(" ", 1)[1]).decode("utf-8")
                    supplied = decoded.partition(":")[2]
                except (ValueError, UnicodeDecodeError):
                    supplied = ""
            if not hmac.compare_digest(supplied, password):
                return JSONResponse(
                    {"detail": "需要 Web 访问密码"},
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="XMU Rollcall"'},
                )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/favicon.ico", include_in_schema=False)
    async def favicon():
        return Response(status_code=204)

    @app.get("/api/health")
    async def health():
        return {"ok": True, "service": "xmu-rollcall-web", "version": __version__}

    @app.get("/api/status")
    async def status():
        return await asyncio.to_thread(manager.public_status)

    async def call_service(fn, *args, **kwargs):
        try:
            return await asyncio.to_thread(fn, *args, **kwargs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @app.post("/api/login")
    async def login(body: LoginBody):
        return await call_service(manager.login, body.username, body.password)

    @app.post("/api/session/restore")
    async def restore():
        return await call_service(manager.restore)

    @app.post("/api/session/disconnect")
    async def disconnect():
        return await call_service(manager.disconnect)

    @app.post("/api/monitor/start")
    async def start_monitor(body: MonitorBody):
        return await call_service(manager.start_monitor, body.auto_submit)

    @app.post("/api/monitor/stop")
    async def stop_monitor():
        return await call_service(manager.stop_monitor)

    @app.put("/api/settings")
    async def settings(body: SettingsBody):
        return await call_service(manager.set_settings, body.threshold, body.auto_submit)

    @app.get("/api/courses")
    async def courses():
        return {"courses": await call_service(manager.list_courses)}

    @app.put("/api/location-binding")
    async def location_binding(body: LocationBindingBody):
        return await call_service(
            manager.bind_location,
            body.course_id,
            body.course_title,
            body.preset_id,
        )

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description="XMU 畅课签到本地 Web 控制台")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址；手机访问时使用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--reload", action="store_true", help="开发时启用自动重载")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.environ.get("XMU_ROLLCALL_WEB_PASSWORD"):
        parser.error("监听局域网地址前，请先设置 XMU_ROLLCALL_WEB_PASSWORD")
    uvicorn.run(
        "xmu_rollcall.web.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        workers=1,
    )


if __name__ == "__main__":
    main()
