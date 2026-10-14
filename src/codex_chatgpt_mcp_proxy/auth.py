"""External OAuth resource server; no local authorization-server implementation."""

import asyncio
import hmac
import ipaddress
import json
import logging
import time
import uuid
from collections import deque
from urllib.parse import urlsplit

import httpx
import jwt
from starlette.responses import JSONResponse

log = logging.getLogger("bridge.http")


class AuthError(Exception):
    def __init__(self, status=401):
        self.status = status


class OAuthVerifier:
    def __init__(self, config):
        self.config = config
        self.keys = {}
        self.expires = 0
        self.lock = asyncio.Lock()

    async def verify(self, token):
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256" or not header.get("kid"):
                raise AuthError()
            async with self.lock:
                if time.time() > self.expires:
                    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                        r = await client.get(self.config.jwks_url)
                        r.raise_for_status()
                        self.keys = {
                            k["kid"]: jwt.PyJWK.from_dict(k).key
                            for k in r.json()["keys"]
                            if k.get("kty") == "RSA" and k.get("use", "sig") == "sig"
                        }
                        self.expires = time.time() + 300
            key = self.keys.get(header["kid"])
            if key is None:
                raise AuthError()
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.config.audience,
                issuer=self.config.issuer,
                options={"require": ["exp", "iat", "iss", "aud", "sub"]},
            )
            if claims["sub"] != self.config.owner_subject:
                raise AuthError(403)
            if self.config.scope not in claims.get("scope", "").split():
                raise AuthError(403)
        except AuthError:
            raise
        except (jwt.PyJWTError, httpx.HTTPError, ValueError, KeyError):
            raise AuthError() from None


class Guard:
    """ASGI gate before parsing requests; applies equally to MCP and worker API."""

    def __init__(self, app, config):
        self.app, self.config = app, config
        self.oauth = OAuthVerifier(config)
        self.buckets = {}
        self.last_sweep = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode().lower(): v.decode() for k, v in scope.get("headers", [])}
        path = scope["path"]
        origin = self.config.public_url.rstrip("/")
        host = urlsplit(origin).netloc
        if headers.get("host") != host or (headers.get("origin") and headers["origin"] != origin):
            return await JSONResponse({"error": "Host or Origin rejected"}, 403)(
                scope, receive, send
            )
        if self.config.mode == "development":
            peer = scope.get("client", ("",))[0]
            try:
                local = peer == "testclient" or ipaddress.ip_address(peer).is_loopback
            except ValueError:
                local = False
            if not local:
                return await JSONResponse({"error": "Development is loopback only"}, 403)(
                    scope, receive, send
                )
        if path == "/mcp" or path.startswith("/mcp/") or path.startswith("/api/worker"):
            token = headers.get("authorization", "")
            if not token.startswith("Bearer "):
                return await self.reject(scope, receive, send, 401)
            token = token[7:]
            try:
                if path.startswith("/api/worker"):
                    if not hmac.compare_digest(token, self.config.worker_token):
                        raise AuthError()
                    principal = "worker"
                elif self.config.mode == "development":
                    if not hmac.compare_digest(token, self.config.dev_mcp_token):
                        raise AuthError()
                    principal = "mcp"
                else:
                    await self.oauth.verify(token)
                    principal = "mcp"
            except AuthError as e:
                return await self.reject(scope, receive, send, e.status)
        else:
            principal = scope.get("client", ("unknown",))[0]
        now = time.monotonic()
        if now - self.last_sweep > 60:
            self.buckets = {k: v for k, v in self.buckets.items() if v and now - v[-1] < 60}
            self.last_sweep = now
        if principal not in self.buckets and len(self.buckets) >= 1000:
            return await JSONResponse({"error": "Rate limited"}, 429)(scope, receive, send)
        bucket = self.buckets.setdefault(principal, deque())
        while bucket and now - bucket[0] > 60:
            bucket.popleft()
        if len(bucket) >= self.config.requests_per_minute:
            return await JSONResponse(
                {"error": "Rate limited"}, 429, headers={"Retry-After": "60"}
            )(scope, receive, send)
        bucket.append(now)
        # Bound body before SDK/FastAPI parsing, including chunked bodies.
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body.extend(message.get("body", b""))
            if len(body) > 131072:
                return await JSONResponse({"error": "Request too large"}, 413)(scope, receive, send)
            if not message.get("more_body", False):
                break
        sent = False

        async def replay():
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        correlation = str(uuid.uuid4())
        status = 500

        async def wrap_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"].append((b"x-correlation-id", correlation.encode()))
            await send(message)

        try:
            await self.app(scope, replay, wrap_send)
        finally:
            # No token, body, query, instruction, result or client address in log.
            log.info(
                json.dumps(
                    {
                        "correlation_id": correlation,
                        "route": "worker"
                        if path.startswith("/api/worker")
                        else "mcp"
                        if path.startswith("/mcp")
                        else "public",
                        "status": status,
                    }
                )
            )

    async def reject(self, scope, receive, send, status):
        challenge = f'Bearer resource_metadata="{self.config.public_url}/.well-known/oauth-protected-resource"'
        if status == 403:
            challenge += ', error="insufficient_scope", scope="bridge:access"'
        await JSONResponse(
            {"error": "Unauthorized" if status == 401 else "Forbidden"},
            status,
            headers={"WWW-Authenticate": challenge},
        )(scope, receive, send)
