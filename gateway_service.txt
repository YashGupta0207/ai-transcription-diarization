"""
Gateway proxy service — the heart of the system.

Flow (matches the spec exactly):
Developer -> dev_ token -> Gateway -> validate token -> decrypt credentials
-> call provider -> return response.

This file contains ZERO provider-specific logic. It only orchestrates:
resolve token -> resolve adapter -> decrypt creds -> adapter builds request
-> send -> stream response back -> log the request.
"""
import time
from datetime import datetime, timezone
import json

import httpx
from fastapi import HTTPException, status, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from starlette.requests import Request
import websockets
import asyncio

from app.adapters.registry import registry
from app.core.encryption import cipher, EncryptionError
from app.models.models import ApiRequestLog, DeveloperToken, Provider, ProviderProfileCredential
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import func, select

_client = httpx.AsyncClient(timeout=httpx.Timeout(connect=10.0, read=120.0, write=60.0, pool=10.0))


async def proxy_request(
    *, db: AsyncSession, token: DeveloperToken, provider: Provider, profile_id, incoming: Request, path: str,
) -> Response:
    # provider.adapter_key doubles as "provider_type" in the admin UI. Any
    # value that isn't one of the specialized adapters below transparently
    # falls back to the generic REST adapter — this is what makes brand-new
    # providers work without a backend code change.
    adapter = registry.get_or_generic(provider.adapter_key)
    client_ip = incoming.client.host if incoming.client else None
    user_agent = incoming.headers.get("user-agent")
    await _enforce_limits(db, token)

    try:
        # Dynamic credentials: N rows of (variable_name, encrypted_value) ->
        # one flat {variable_name: decrypted_value} dict, exactly the shape
        # every adapter's build_request() expects. The Gateway never knows
        # or cares what the variable names are.
        cred_rows = (await db.execute(select(ProviderProfileCredential).where(ProviderProfileCredential.profile_id == profile_id))).scalars().all()
        credentials = {row.variable_name: cipher.decrypt(row.encrypted_value) for row in cred_rows}
    except EncryptionError as exc:
        await _log(db, token, provider.id, path, incoming.method, None, None, str(exc), ip_address=client_ip, user_agent=user_agent)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Credential decryption failed") from exc


    body = await incoming.body()
    request_size = len(body)

    try:
        built = await adapter.build_request(incoming=incoming, path=path, body=body, credentials=credentials)
    except ValueError as exc:
        await _log(db, token, provider.id, path, incoming.method, None, None, str(exc), request_size, ip_address=client_ip, user_agent=user_agent)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Provider misconfigured: {exc}") from exc

    start = time.perf_counter()
    try:
        upstream = await adapter.send(_client, built)
    except httpx.HTTPError as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await _log(db, token, provider.id, path, incoming.method, None, latency_ms, str(exc),
                    request_size, ip_address=client_ip, user_agent=user_agent)
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Upstream provider request failed: {exc}") from exc

    latency_ms = int((time.perf_counter() - start) * 1000)

    token.last_used_at = datetime.now(timezone.utc)
    token.last_client_ip = client_ip
    token.last_user_agent = user_agent
    await db.commit()

    if built.is_streaming:
        async def _stream():
            response_bytes = 0
            try:
                async for chunk in upstream.aiter_raw():
                    response_bytes += len(chunk)
                    yield chunk
            finally:
                await upstream.aclose()
                await _log(db, token, provider.id, path, incoming.method, upstream.status_code,
                            latency_ms, None, request_size, response_bytes, ip_address=client_ip,
                            user_agent=user_agent, is_streaming=True)

        return StreamingResponse(
            _stream(),
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    content = await upstream.aread()
    await upstream.aclose()
    usage = adapter.normalize_usage(content)
    await _log(db, token, provider.id, path, incoming.method, upstream.status_code,
                latency_ms, None, request_size, len(content), ip_address=client_ip,
                user_agent=user_agent, usage=usage)

    return Response(
        content=content,
        status_code=upstream.status_code,
        media_type=upstream.headers.get("content-type"),
    )


async def _log(db, token, provider_id, path, method, status_code, latency_ms,
                error_message, request_size=None, response_size=None, ip_address=None,
                user_agent=None, is_streaming=False, usage=None):
    usage = usage or {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "estimated_cost": 0.0}
    entry = ApiRequestLog(
        developer_token_id=token.id if token else None,
        provider_id=provider_id,
        endpoint=path,
        method=method,
        status_code=status_code,
        latency_ms=latency_ms,
        request_size_bytes=request_size,
        response_size_bytes=response_size,
        error_message=error_message,
        ip_address=ip_address,
        user_agent=user_agent,
        is_streaming=is_streaming,
        prompt_tokens=usage["prompt_tokens"], completion_tokens=usage["completion_tokens"],
        total_tokens=usage["total_tokens"], estimated_cost=usage["estimated_cost"],
    )
    db.add(entry)
    if token is not None:
        token.total_requests += 1
        if status_code is not None and status_code < 400:
            token.successful_requests += 1
        else:
            token.failed_requests += 1
        if token.first_used_at is None:
            token.first_used_at = datetime.now(timezone.utc)
        token.prompt_tokens += usage["prompt_tokens"]
        token.completion_tokens += usage["completion_tokens"]
        token.total_tokens += usage["total_tokens"]
        token.total_latency_ms += latency_ms or 0
        token.estimated_cost += usage["estimated_cost"]
    await db.commit()


async def _enforce_limits(db: AsyncSession, token: DeveloperToken) -> None:
    """Check persisted request history before any upstream request is built/sent."""
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    async def totals(since):
        row = (await db.execute(select(func.count(ApiRequestLog.id), func.coalesce(func.sum(ApiRequestLog.total_tokens), 0)).where(
            ApiRequestLog.developer_token_id == token.id, ApiRequestLog.created_at >= since
        ))).one()
        return int(row[0]), int(row[1])

    day_requests, day_tokens = await totals(day_start)
    month_requests, month_tokens = await totals(month_start)
    checks = (
        (token.daily_request_limit, day_requests, "Daily request limit exceeded."),
        (token.monthly_request_limit, month_requests, "Monthly request limit exceeded."),
        (token.daily_token_limit, day_tokens, "Daily token limit exceeded."),
        (token.monthly_token_limit, month_tokens, "Monthly token limit exceeded."),
    )
    for limit, current, message in checks:
        if limit is not None and current >= limit:
            raise HTTPException(status_code=429, detail=message)


async def proxy_websocket(
    *, db: AsyncSession, token: DeveloperToken, provider: Provider, profile_id, websocket: WebSocket, path: str,
) -> None:
    adapter = registry.get_or_generic(provider.adapter_key)
    client_ip = websocket.client.host if websocket.client else None
    user_agent = websocket.headers.get("user-agent")
    
    try:
        await _enforce_limits(db, token)
    except HTTPException as exc:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION, reason=exc.detail)
        return

    try:
        cred_rows = (await db.execute(select(ProviderProfileCredential).where(ProviderProfileCredential.profile_id == profile_id))).scalars().all()
        credentials = {row.variable_name: cipher.decrypt(row.encrypted_value) for row in cred_rows}
    except EncryptionError as exc:
        await _log(db, token, provider.id, path, "WEBSOCKET", None, None, str(exc), ip_address=client_ip, user_agent=user_agent)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Credential decryption failed")
        return

    try:
        upstream_url, upstream_headers = adapter.build_websocket_request(incoming=websocket, path=path, credentials=credentials)
    except NotImplementedError as exc:
        await websocket.close(code=status.WS_1003_UNSUPPORTED_DATA, reason=str(exc))
        return
    except ValueError as exc:
        await _log(db, token, provider.id, path, "WEBSOCKET", None, None, str(exc), ip_address=client_ip, user_agent=user_agent)
        await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason=f"Provider misconfigured: {exc}")
        return

    start = time.perf_counter()
    
    try:
        async with websockets.connect(upstream_url, additional_headers=upstream_headers) as upstream_ws:
            token.last_used_at = datetime.now(timezone.utc)
            token.last_client_ip = client_ip
            token.last_user_agent = user_agent
            await db.commit()

            async def forward_client_to_upstream():
                try:
                    while True:
                        message = await websocket.receive()
                        if "text" in message:
                            await upstream_ws.send(message["text"])
                        elif "bytes" in message:
                            await upstream_ws.send(message["bytes"])
                except WebSocketDisconnect:
                    pass
                except Exception as e:
                    pass
                finally:
                    await upstream_ws.close()

            async def forward_upstream_to_client():
                try:
                    async for message in upstream_ws:
                        if isinstance(message, str):
                            await websocket.send_text(message)
                        else:
                            await websocket.send_bytes(message)
                except websockets.exceptions.ConnectionClosed:
                    pass
                except Exception as e:
                    pass
                finally:
                    try:
                        await websocket.close()
                    except RuntimeError:
                        pass

            await asyncio.gather(
                forward_client_to_upstream(),
                forward_upstream_to_client(),
            )
            
            latency_ms = int((time.perf_counter() - start) * 1000)
            await _log(db, token, provider.id, path, "WEBSOCKET", 101, latency_ms, None, ip_address=client_ip, user_agent=user_agent)
            
    except Exception as exc:
        latency_ms = int((time.perf_counter() - start) * 1000)
        await _log(db, token, provider.id, path, "WEBSOCKET", None, latency_ms, str(exc), ip_address=client_ip, user_agent=user_agent)
        try:
            await websocket.close(code=status.WS_1011_INTERNAL_ERROR, reason="Upstream connection failed")
        except RuntimeError:
            pass



