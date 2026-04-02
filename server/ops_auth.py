from __future__ import annotations

from collections.abc import Callable
from time import time

from fastapi import Header, HTTPException, Request, status

from server.ops_models import ActorIdentity
from server.ops_service import OpsControlPlaneService


_RATE_LIMIT_BUCKETS: dict[tuple[str, str], list[float]] = {}


def make_auth_dependency(
    control_plane: OpsControlPlaneService,
    required_roles: set[str] | None = None,
) -> Callable[..., ActorIdentity]:
    async def dependency(
        request: Request,
        authorization: str | None = Header(default=None),
        x_tenant_id: str | None = Header(default=None),
    ) -> ActorIdentity:
        config = control_plane.config
        tenant_id = x_tenant_id or "default"
        if config.disable_auth_for_local_dev:
            actor = ActorIdentity(
                actor_id="local-dev",
                roles=["viewer", "agent", "operator", "approver", "admin"],
                allowed_tenants=["*"],
                tenant_id=tenant_id,
            )
            _check_roles(actor, required_roles)
            _check_tenant(actor, tenant_id)
            _check_rate_limit(control_plane, actor, request.url.path)
            return actor

        if not config.require_auth:
            actor = ActorIdentity(
                actor_id="anonymous",
                roles=["viewer", "agent"],
                allowed_tenants=["default"],
                tenant_id=tenant_id,
            )
            _check_roles(actor, required_roles)
            _check_tenant(actor, tenant_id)
            _check_rate_limit(control_plane, actor, request.url.path)
            return actor

        if not config.auth_configured:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "OPS_API_TOKENS_JSON is not configured for the ops control plane",
            )

        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token")

        token = authorization.split(" ", 1)[1].strip()
        actor = config.api_tokens.get(token)
        if actor is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")

        scoped_actor = actor.model_copy(update={"tenant_id": tenant_id})
        _check_roles(scoped_actor, required_roles)
        _check_tenant(scoped_actor, tenant_id)
        _check_rate_limit(control_plane, scoped_actor, request.url.path)
        return scoped_actor

    return dependency


def _check_roles(actor: ActorIdentity, required_roles: set[str] | None) -> ActorIdentity:
    if required_roles and not required_roles.intersection(actor.roles):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Missing required role. Need one of: {', '.join(sorted(required_roles))}",
        )
    return actor


def _check_tenant(actor: ActorIdentity, tenant_id: str) -> None:
    if "*" in actor.allowed_tenants:
        return
    if tenant_id not in actor.allowed_tenants:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Actor {actor.actor_id} is not allowed to access tenant {tenant_id}",
        )


def _check_rate_limit(control_plane: OpsControlPlaneService, actor: ActorIdentity, path: str) -> None:
    if not {"admin", "approver"}.intersection(actor.roles):
        return
    now = time()
    key = (actor.actor_id, path)
    window = control_plane.config.admin_rate_limit_window_seconds
    limit = control_plane.config.admin_rate_limit_count
    bucket = [timestamp for timestamp in _RATE_LIMIT_BUCKETS.get(key, []) if now - timestamp < window]
    if len(bucket) >= limit:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Admin rate limit exceeded for {path}; retry after {window} seconds",
        )
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket
