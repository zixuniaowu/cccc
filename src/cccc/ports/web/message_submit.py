from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable, Dict

from fastapi import HTTPException

from ...daemon.server import get_daemon_endpoint

logger = logging.getLogger("cccc.web.messaging")


def accepted_message_result(*, group_id: str, client_id: str) -> Dict[str, Any]:
    return {
        "ok": True,
        "result": {
            "accepted": True,
            "queued": True,
            "group_id": str(group_id or "").strip(),
            "client_id": str(client_id or "").strip(),
            "event": None,
            "ack_event": None,
        },
    }


async def ensure_daemon_ready() -> None:
    ep = get_daemon_endpoint()
    transport = str(ep.get("transport") or "").strip().lower()
    try:
        if transport == "tcp":
            host = str(ep.get("host") or "127.0.0.1").strip() or "127.0.0.1"
            port = int(ep.get("port") or 0)
            if port <= 0:
                raise RuntimeError("invalid daemon tcp endpoint")
            _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=0.05)
        else:
            path = str(ep.get("path") or "").strip()
            if not path:
                raise RuntimeError("missing daemon socket path")
            _reader, writer = await asyncio.wait_for(asyncio.open_unix_connection(path), timeout=0.05)
        writer.close()
        await writer.wait_closed()
        return
    except Exception:
        pass
    raise HTTPException(
        status_code=503,
        detail={
            "code": "daemon_unavailable",
            "message": "ccccd unavailable",
            "details": {"endpoint": ep},
        },
    )


async def submit_message_request(
    *,
    submit_mode: str,
    daemon: Callable[..., Awaitable[Dict[str, Any]]],
    req: Dict[str, Any],
    group_id: str,
    client_id: str,
) -> Dict[str, Any]:
    mode = str(submit_mode or "async").strip().lower() or "async"
    if mode == "sync":
        return await daemon(req)
    await ensure_daemon_ready()
    asyncio.create_task(_submit_daemon_request(daemon=daemon, req=req))
    return accepted_message_result(group_id=group_id, client_id=client_id)


async def _submit_daemon_request(
    *,
    daemon: Callable[..., Awaitable[Dict[str, Any]]],
    req: Dict[str, Any],
) -> None:
    try:
        resp = await daemon(req)
        if not bool(resp.get("ok")):
            err = resp.get("error") if isinstance(resp.get("error"), dict) else {}
            logger.warning(
                "async message submit failed op=%s code=%s message=%s",
                str(req.get("op") or ""),
                str(err.get("code") or ""),
                str(err.get("message") or ""),
            )
    except Exception as exc:
        logger.warning("async message submit exception op=%s err=%s", str(req.get("op") or ""), exc)
