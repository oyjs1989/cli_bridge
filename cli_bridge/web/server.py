"""FastAPI-based web console for cli-bridge."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from cli_bridge.web._console_service import (
    MODEL_CONTEXT_SIZES,
    REFERENCE_MODELS,
    RUNTIME_MODES,
    ConsoleService,
    _display_model_name,
)
from cli_bridge.web._routes import register_routes

__all__ = ["ConsoleService", "create_app", "run_console"]


def create_app(token: str | None = None) -> FastAPI:
    service = ConsoleService()
    app = FastAPI(title="cli-bridge Console", version="1.0.0")

    base_dir = Path(__file__).parent
    templates = Jinja2Templates(directory=str(base_dir / "templates"))
    app.mount("/static", StaticFiles(directory=str(base_dir / "static")), name="static")

    class _WebConsoleLogHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            try:
                message = self.format(record)
                service.add_web_log(message)
            except Exception:
                pass

    log_handler = _WebConsoleLogHandler()
    log_handler.setLevel(logging.DEBUG)
    log_handler.setFormatter(logging.Formatter("%(name)s | %(levelname)s | %(message)s"))
    for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "cli_bridge"):
        logging.getLogger(logger_name).addHandler(log_handler)

    @app.middleware("http")
    async def _request_logger(request: Request, call_next):
        start = datetime.now()
        try:
            response = await call_next(request)
            elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
            service.add_web_log(f"{request.method} {request.url.path} -> {response.status_code} ({elapsed_ms}ms)")
            return response
        except Exception as e:
            elapsed_ms = int((datetime.now() - start).total_seconds() * 1000)
            service.add_web_log(f"{request.method} {request.url.path} -> 500 ({elapsed_ms}ms) err={e}")
            raise

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        for logger_name in ("uvicorn", "uvicorn.error", "uvicorn.access", "cli_bridge"):
            logger = logging.getLogger(logger_name)
            if log_handler in logger.handlers:
                logger.removeHandler(log_handler)
        await service.close_chat_adapter()

    def _check_token(request: Request) -> None:
        if not token:
            return
        supplied = (
            request.headers.get("x-cli-bridge-console-token")
            or request.query_params.get("token")
            or request.cookies.get("cli_bridge_console_token")
        )
        if supplied != token:
            raise HTTPException(status_code=401, detail="Unauthorized")

    def _resolve_web_session_id(request: Request) -> str:
        sid = request.cookies.get("cli_bridge_web_session")
        if sid:
            return sid
        return uuid.uuid4().hex[:16]

    # Register dashboard, conversations, config, logs, and MCP routes
    register_routes(app, service, templates, token, _check_token)

    @app.get("/chat", response_class=HTMLResponse)
    async def web_chat(request: Request) -> HTMLResponse:
        _check_token(request)
        session_id = _resolve_web_session_id(request)
        cfg = service.get_config_obj()
        default_model_raw = cfg.get_model()
        default_model = _display_model_name(default_model_raw)
        model_options = []
        for m in [default_model, *REFERENCE_MODELS]:
            if m not in model_options:
                model_options.append(m)
        enabled_channels = cfg.get_enabled_channels()
        if "web" not in enabled_channels:
            enabled_channels = ["web", *enabled_channels]
        chat_targets = service.list_chat_targets(limit=200)

        response = templates.TemplateResponse(
            "chat.html",
            {
                "request": request,
                "page": "chat",
                "token": token or "",
                "session_id": session_id,
                "messages": service.get_or_load_chat_messages("web", session_id),
                "default_model": default_model,
                "default_model_raw": default_model_raw,
                "driver_backend": cfg.driver.backend,
                "default_runtime_mode": "yolo" if (cfg.driver.iflow and cfg.driver.iflow.yolo) else "default",
                "default_think_enabled": cfg.driver.iflow.thinking if cfg.driver.iflow else False,
                "model_options": model_options,
                "model_context_sizes": MODEL_CONTEXT_SIZES,
                "runtime_modes": RUNTIME_MODES,
                "enabled_channels": enabled_channels,
                "chat_targets": chat_targets,
            },
        )
        response.set_cookie("cli_bridge_web_session", session_id, httponly=False, max_age=86400 * 30)
        return response

    @app.post("/api/chat/send")
    async def web_chat_send(
        request: Request,
        message: str = Form(...),
        session_id: str = Form(...),
    ) -> JSONResponse:
        _check_token(request)
        text = message.strip()
        if not text:
            return JSONResponse({"ok": False, "message": "消息不能为空"}, status_code=400)
        try:
            reply = await service.send_web_chat_message(session_id, text)
            return JSONResponse(
                {
                    "ok": True,
                    "reply": reply,
                    "messages": service.get_web_chat_messages(session_id),
                }
            )
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

    @app.post("/api/chat/stream")
    async def web_chat_stream(
        request: Request,
        message: str = Form(...),
        session_id: str = Form(...),
        channel: str = Form(default="web"),
        chat_id: str = Form(default=""),
        model: str = Form(default=""),
        think_enabled: int = Form(default=0),
        runtime_mode: str = Form(default="yolo"),
    ) -> StreamingResponse:
        _check_token(request)
        text = message.strip()
        if not text:
            raise HTTPException(status_code=400, detail="消息不能为空")

        async def event_gen():
            async for event in service.stream_web_chat(
                session_id=session_id,
                message=text,
                channel=channel,
                chat_id=chat_id,
                model=model,
                think_enabled=bool(think_enabled),
                mode=runtime_mode,
            ):
                payload = json.dumps(event["data"], ensure_ascii=False)
                yield f"event: {event['event']}\ndata: {payload}\n\n"

        return StreamingResponse(
            event_gen(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/api/chat/reset")
    async def web_chat_reset(
        request: Request,
        session_id: str = Form(...),
        channel: str = Form(default="web"),
        chat_id: str = Form(default=""),
    ) -> JSONResponse:
        _check_token(request)
        effective_channel = (channel or "web").strip()
        effective_chat_id = (chat_id or session_id).strip()
        key = f"{effective_channel}:{effective_chat_id}"
        service._web_chat_messages[key] = []
        adapter = await service._get_chat_adapter()
        adapter.session_mappings.clear_session(effective_channel, effective_chat_id)
        return JSONResponse({"ok": True})

    @app.get("/api/chat/history")
    async def web_chat_history(
        request: Request,
        channel: str = Query(default="web"),
        chat_id: str = Query(default=""),
        session_id: str = Query(default=""),
    ) -> JSONResponse:
        _check_token(request)
        sid = (session_id or "").strip() or _resolve_web_session_id(request)
        effective_channel = (channel or "web").strip()
        effective_chat_id = (chat_id or sid).strip()
        messages = service.get_or_load_chat_messages(effective_channel, effective_chat_id)
        return JSONResponse(
            {
                "ok": True,
                "channel": effective_channel,
                "chat_id": effective_chat_id,
                "messages": messages,
            }
        )

    @app.post("/api/chat/target/pin")
    async def web_chat_target_pin(
        request: Request,
        channel: str = Form(...),
        chat_id: str = Form(...),
        pinned: int = Form(default=1),
    ) -> JSONResponse:
        _check_token(request)
        effective_channel = (channel or "").strip()
        effective_chat_id = (chat_id or "").strip()
        if not effective_channel or not effective_chat_id:
            return JSONResponse({"ok": False, "message": "channel/chat_id 不能为空"}, status_code=400)
        service.set_chat_target_pinned(effective_channel, effective_chat_id, bool(pinned))
        return JSONResponse({"ok": True, "pinned": bool(pinned)})

    @app.post("/api/chat/target/delete")
    async def web_chat_target_delete(
        request: Request,
        channel: str = Form(...),
        chat_id: str = Form(...),
    ) -> JSONResponse:
        _check_token(request)
        effective_channel = (channel or "").strip()
        effective_chat_id = (chat_id or "").strip()
        if not effective_channel or not effective_chat_id:
            return JSONResponse({"ok": False, "message": "channel/chat_id 不能为空"}, status_code=400)
        deleted = service.delete_chat_target(effective_channel, effective_chat_id)
        adapter = await service._get_chat_adapter()
        adapter.session_mappings.clear_session(effective_channel, effective_chat_id)
        return JSONResponse({"ok": True, "deleted": deleted})

    return app


def run_console(host: str = "127.0.0.1", port: int = 8787, token: str | None = None) -> None:
    import uvicorn

    app = create_app(token=token)
    uvicorn.run(app, host=host, port=port, log_level="info")
