"""Non-chat FastAPI route registrations for the web console."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from cli_bridge.config.schema import Config
from cli_bridge.web._console_service import ConsoleService, _coerce_field_value, _flatten_dict


def register_routes(
    app: FastAPI,
    svc: ConsoleService,
    templates: Jinja2Templates,
    token: str | None,
    check_token: Callable[[Request], None],
) -> None:
    """Register all non-chat web console routes on *app*."""

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        check_token(request)
        gateway = svc.get_gateway_status()
        channels = svc.get_channels_summary()
        logs = svc.read_log_tail(limit=120)
        errors = [line for line in logs if "ERROR" in line or "Traceback" in line][-20:]

        conversation_total = sum(item["conversation_count"] for item in channels)
        message_total = sum(item["message_count"] for item in channels)
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "page": "dashboard",
                "gateway": gateway,
                "channels": channels,
                "conversation_total": conversation_total,
                "message_total": message_total,
                "errors": errors,
                "token": token or "",
            },
        )

    @app.get("/conversations", response_class=HTMLResponse)
    async def conversations(
        request: Request,
        channel: str = Query(default=""),
        keyword: str = Query(default=""),
    ) -> HTMLResponse:
        check_token(request)
        items = svc.list_conversations(channel=channel, keyword=keyword)
        channel_names = sorted({i["channel"] for i in svc.list_conversations()})
        return templates.TemplateResponse(
            "conversations.html",
            {
                "request": request,
                "page": "conversations",
                "items": items,
                "channel": channel,
                "keyword": keyword,
                "channel_names": channel_names,
                "token": token or "",
            },
        )

    @app.get("/conversations/{channel}/{file_name}", response_class=HTMLResponse)
    async def conversation_detail(request: Request, channel: str, file_name: str) -> HTMLResponse:
        check_token(request)
        try:
            detail = svc.get_conversation_detail(channel, file_name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Conversation not found") from exc
        return templates.TemplateResponse(
            "conversation_detail.html",
            {
                "request": request,
                "page": "conversations",
                "detail": detail,
                "token": token or "",
            },
        )

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request, saved: str = "") -> HTMLResponse:
        check_token(request)
        cfg = svc.get_config_obj()
        channel_states = svc.get_channel_states(cfg)
        enabled_count = len([c for c in channel_states if c["enabled"]])
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "page": "config",
                "config_text": svc.read_config_text(),
                "saved": saved,
                "channel_states": channel_states,
                "enabled_count": enabled_count,
                "channel_total": len(channel_states),
                "driver_backend": cfg.driver.backend,
                "driver_model": cfg.get_model(),
                "driver_timeout": cfg.driver.timeout,
                "driver_think": cfg.driver.iflow.thinking if cfg.driver.iflow else False,
                "driver_yolo": cfg.driver.iflow.yolo if cfg.driver.iflow else False,
                "config_path": str(svc.config_path),
                "token": token or "",
            },
        )

    @app.post("/config/save")
    async def config_save(request: Request, content: str = Form(...)) -> HTMLResponse:
        check_token(request)
        ok, message = svc.validate_and_save_config(content)
        if ok:
            return RedirectResponse(url="/config?saved=1", status_code=303)
        cfg = svc.get_config_obj()
        channel_states = svc.get_channel_states(cfg)
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "page": "config",
                "config_text": content,
                "error": message,
                "channel_states": channel_states,
                "enabled_count": len([c for c in channel_states if c["enabled"]]),
                "channel_total": len(channel_states),
                "driver_backend": cfg.driver.backend,
                "driver_model": cfg.get_model(),
                "driver_timeout": cfg.driver.timeout,
                "driver_think": cfg.driver.iflow.thinking if cfg.driver.iflow else False,
                "driver_yolo": cfg.driver.iflow.yolo if cfg.driver.iflow else False,
                "config_path": str(svc.config_path),
                "token": token or "",
            },
            status_code=400,
        )

    @app.post("/api/config/validate")
    async def config_validate(request: Request, content: str = Form(...)) -> JSONResponse:
        check_token(request)
        try:
            raw = json.loads(content)
            Config(**raw)
            return JSONResponse({"ok": True, "message": "配置校验通过"})
        except json.JSONDecodeError as e:
            return JSONResponse({"ok": False, "message": f"JSON 解析失败: {e}"}, status_code=400)
        except Exception as e:
            return JSONResponse({"ok": False, "message": f"配置校验失败: {e}"}, status_code=400)

    @app.post("/config/toggle/{channel_name}")
    async def config_toggle(
        request: Request,
        channel_name: str,
        enabled: int = Form(...),
    ) -> RedirectResponse:
        check_token(request)
        svc.set_channel_enabled(channel_name, bool(enabled))
        url = "/config?saved=1"
        if token:
            url += f"&token={token}"
        return RedirectResponse(url=url, status_code=303)

    @app.post("/config/channel/{channel_name}")
    async def config_channel_save(
        request: Request,
        channel_name: str,
    ) -> HTMLResponse:
        check_token(request)
        cfg = svc.get_config_obj()
        channel_obj = getattr(cfg.channels, channel_name, None)
        if channel_obj is None:
            raise HTTPException(status_code=404, detail=f"未知渠道: {channel_name}")

        existing = channel_obj.model_dump()
        flat_fields = _flatten_dict(existing)
        field_index = {path.replace(".", "__"): (path, sample) for path, sample in flat_fields}

        form = await request.form()
        updates: dict[str, Any] = {}
        for form_name, raw_value in form.multi_items():
            if form_name not in field_index:
                continue
            path, sample = field_index[form_name]
            try:
                updates[path] = _coerce_field_value(str(raw_value), sample)
            except Exception as e:
                channel_states = svc.get_channel_states(cfg)
                return templates.TemplateResponse(
                    "config.html",
                    {
                        "request": request,
                        "page": "config",
                        "config_text": svc.read_config_text(),
                        "saved": "",
                        "error": f"{channel_name} 字段 {path} 值无效: {e}",
                        "channel_states": channel_states,
                        "enabled_count": len([c for c in channel_states if c["enabled"]]),
                        "channel_total": len(channel_states),
                        "driver_backend": cfg.driver.backend,
                        "driver_model": cfg.get_model(),
                        "driver_timeout": cfg.driver.timeout,
                        "driver_think": cfg.driver.iflow.thinking if cfg.driver.iflow else False,
                        "driver_yolo": cfg.driver.iflow.yolo if cfg.driver.iflow else False,
                        "config_path": str(svc.config_path),
                        "token": token or "",
                    },
                    status_code=400,
                )

        ok, message = svc.update_channel_config(channel_name, updates)
        if ok:
            url = f"/config?saved={channel_name}"
            if token:
                url += f"&token={token}"
            return RedirectResponse(url=url, status_code=303)
        channel_states = svc.get_channel_states(cfg)
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "page": "config",
                "config_text": svc.read_config_text(),
                "saved": "",
                "error": message,
                "channel_states": channel_states,
                "enabled_count": len([c for c in channel_states if c["enabled"]]),
                "channel_total": len(channel_states),
                "driver_backend": cfg.driver.backend,
                "driver_model": cfg.get_model(),
                "driver_timeout": cfg.driver.timeout,
                "driver_think": cfg.driver.iflow.thinking if cfg.driver.iflow else False,
                "driver_yolo": cfg.driver.iflow.yolo if cfg.driver.iflow else False,
                "config_path": str(svc.config_path),
                "token": token or "",
            },
            status_code=400,
        )

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(
        request: Request,
        keyword: str = Query(default=""),
        source: str = Query(default="gateway"),
        auto: int = Query(default=1),
    ) -> HTMLResponse:
        check_token(request)
        lines, cursor = svc.read_logs(source=source, limit=600, since=0)
        if keyword:
            lines = [line for line in lines if keyword.lower() in line.lower()]
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "page": "logs",
                "lines": lines[-400:],
                "keyword": keyword,
                "source": source,
                "auto": bool(auto),
                "cursor": cursor,
                "token": token or "",
            },
        )

    @app.get("/api/logs/tail")
    async def api_logs_tail(
        request: Request,
        source: str = Query(default="gateway"),
        keyword: str = Query(default=""),
        since: int = Query(default=0),
        limit: int = Query(default=200),
    ) -> JSONResponse:
        check_token(request)
        safe_limit = max(20, min(limit, 800))
        lines, cursor = svc.read_logs(source=source, limit=safe_limit, since=since)
        if keyword:
            lines = [line for line in lines if keyword.lower() in line.lower()]
        return JSONResponse({"ok": True, "lines": lines, "cursor": cursor, "source": source})

    @app.get("/api/mcp/status")
    async def api_mcp_status(request: Request) -> JSONResponse:
        """获取 MCP 代理状态。"""
        check_token(request)
        status = await svc.get_mcp_proxy_status()
        return JSONResponse({"ok": True, "data": status})

    @app.post("/api/mcp/restart")
    async def api_mcp_restart(request: Request) -> JSONResponse:
        """重启 MCP 代理。"""
        check_token(request)
        from cli_bridge.cli.commands import (
            check_mcp_proxy_running,
            start_mcp_proxy,
            stop_mcp_proxy,
        )

        cfg = svc.get_config_obj()
        port = cfg.mcp_proxy.port if cfg.mcp_proxy else 8888

        try:
            # 先检查是否已经在运行
            if check_mcp_proxy_running(port):
                stop_mcp_proxy()
                await asyncio.sleep(1)

            # 启动新的实例
            if start_mcp_proxy(port):
                return JSONResponse({"ok": True, "message": "MCP 代理已重启"})
            else:
                return JSONResponse({"ok": False, "message": "MCP 代理启动失败"}, status_code=500)
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

    @app.post("/api/mcp/sync")
    async def api_mcp_sync(request: Request) -> JSONResponse:
        """从 iflow CLI 同步 MCP 配置。"""
        check_token(request)
        from cli_bridge.utils.helpers import sync_mcp_from_iflow

        try:
            if sync_mcp_from_iflow(overwrite=False):
                return JSONResponse({"ok": True, "message": "MCP 配置同步成功"})
            else:
                return JSONResponse({"ok": False, "message": "同步失败或无需同步"}, status_code=500)
        except Exception as e:
            return JSONResponse({"ok": False, "message": str(e)}, status_code=500)

    @app.get("/mcp", response_class=HTMLResponse)
    async def mcp_page(request: Request) -> HTMLResponse:
        """MCP 代理状态页面。"""
        check_token(request)
        page_token = request.query_params.get("token", "")
        status = await svc.get_mcp_proxy_status()
        return templates.TemplateResponse(
            "mcp.html",
            {"request": request, "page": "mcp", "mcp_status": status, "token": page_token or ""},
        )
