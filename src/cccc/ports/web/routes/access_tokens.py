from __future__ import annotations

import hashlib
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ....kernel.access_tokens import (
    create_access_token,
    delete_access_token,
    list_access_tokens,
    lookup_access_token,
    update_access_token,
)
from ..schemas import RouteContext, require_admin


class AccessTokenCreateRequest(BaseModel):
    user_id: str
    allowed_groups: List[str] = Field(default_factory=list)
    is_admin: bool = False
    custom_token: Optional[str] = None


class AccessTokenUpdateRequest(BaseModel):
    allowed_groups: Optional[List[str]] = None
    is_admin: Optional[bool] = None


def _token_id(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]


def _resolve_raw_token(token_id: str) -> str:
    target = str(token_id or "").strip()
    if len(target) != 16:
        return ""
    for item in list_access_tokens():
        raw = str((item or {}).get("token") or "").strip()
        if raw and _token_id(raw) == target:
            return raw
    return target


def _mask_entry(item: Dict[str, Any]) -> Dict[str, Any]:
    entry = dict(item)
    raw = str(entry.get("token") or "")
    entry["token_id"] = _token_id(raw) if raw else ""
    entry["token_preview"] = raw[:4] + "..." + raw[-4:] if len(raw) > 8 else "****"
    entry.pop("token", None)
    return entry


def create_routers(ctx: RouteContext) -> list[APIRouter]:
    _ = ctx
    global_router = APIRouter(prefix="/api/v1")

    @global_router.get("/access-tokens", dependencies=[Depends(require_admin)])
    async def access_tokens_list() -> Dict[str, Any]:
        items = [_mask_entry(item) for item in list_access_tokens()]
        return {"ok": True, "result": {"access_tokens": items}}

    @global_router.post("/access-tokens", dependencies=[Depends(require_admin)])
    async def access_tokens_create(req: AccessTokenCreateRequest) -> Dict[str, Any]:
        user_id = str(req.user_id or "").strip()
        if not user_id:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": "user_id is required", "details": {}},
            )
        if not req.is_admin:
            existing = list_access_tokens()
            has_admin = any(bool((item or {}).get("is_admin")) for item in existing if isinstance(item, dict))
            if not has_admin:
                raise HTTPException(
                    status_code=400,
                    detail={
                        "code": "admin_required_first",
                        "message": "The first access token must have admin privileges",
                        "details": {},
                    },
                )
        try:
            entry = create_access_token(
                user_id,
                allowed_groups=list(req.allowed_groups),
                is_admin=bool(req.is_admin),
                custom_token=str(req.custom_token or "").strip() or None,
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": str(exc), "details": {}},
            ) from exc
        return {"ok": True, "result": {"access_token": entry}}

    @global_router.patch("/access-tokens/{token_id}", dependencies=[Depends(require_admin)])
    async def access_tokens_update(token_id: str, req: AccessTokenUpdateRequest) -> Dict[str, Any]:
        raw_token = _resolve_raw_token(token_id)
        if not raw_token:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": "token_id is required", "details": {}},
            )
        entry = update_access_token(
            raw_token,
            allowed_groups=req.allowed_groups,
            is_admin=req.is_admin,
        )
        if entry is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "access token not found", "details": {}},
            )
        return {"ok": True, "result": {"access_token": _mask_entry(entry)}}

    @global_router.get("/access-tokens/{token_id}/reveal", dependencies=[Depends(require_admin)])
    async def access_tokens_reveal(token_id: str) -> Dict[str, Any]:
        raw_token = _resolve_raw_token(token_id)
        if not raw_token:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": "token_id is required", "details": {}},
            )
        if lookup_access_token(raw_token) is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "access token not found", "details": {}},
            )
        return {"ok": True, "result": {"token": raw_token}}

    @global_router.delete("/access-tokens/{token_id}", dependencies=[Depends(require_admin)])
    async def access_tokens_delete(token_id: str) -> Dict[str, Any]:
        raw_token = _resolve_raw_token(token_id)
        if not raw_token:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_request", "message": "token_id is required", "details": {}},
            )
        if not delete_access_token(raw_token):
            raise HTTPException(
                status_code=404,
                detail={"code": "not_found", "message": "access token not found", "details": {}},
            )
        return {"ok": True, "result": {"deleted": True, "access_tokens_remain": bool(list_access_tokens())}}

    return [global_router]
