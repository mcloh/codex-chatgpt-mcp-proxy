"""Environment configuration. Never reads Codex credentials."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


def env(name, default=""):
    return os.environ.get("BRIDGE_" + name, default)


@dataclass
class ServerConfig:
    database: str = field(default_factory=lambda: env("DATABASE", "private/server.sqlite"))
    public_url: str = field(default_factory=lambda: env("PUBLIC_URL", "http://127.0.0.1:8000"))
    mode: str = field(default_factory=lambda: env("MODE", "production"))
    worker_token: str = field(default_factory=lambda: env("WORKER_TOKEN"))
    dev_mcp_token: str = field(default_factory=lambda: env("DEV_MCP_TOKEN"))
    worker_id: str = field(default_factory=lambda: env("WORKER_ID", "home"))
    workspaces: tuple = field(default_factory=lambda: tuple(env("WORKSPACES", "demo").split(",")))
    issuer: str = field(default_factory=lambda: env("OAUTH_ISSUER"))
    jwks_url: str = field(default_factory=lambda: env("OAUTH_JWKS_URL"))
    owner_subject: str = field(default_factory=lambda: env("OWNER_SUBJECT"))
    audience: str = field(default_factory=lambda: env("OAUTH_AUDIENCE"))
    scope: str = "bridge:access"
    lease_seconds: int = 45
    max_timeout: int = 3600
    max_tasks: int = 10000
    max_events: int = 2000
    max_event_bytes: int = 16384
    max_result_chars: int = 64000
    retention_days: int = field(default_factory=lambda: int(env("RETENTION_DAYS", "30")))
    requests_per_minute: int = 240

    def validate(self):
        u = urlsplit(self.public_url)
        if u.username or u.password or u.path not in ("", "/") or u.query or u.fragment:
            raise ValueError("PUBLIC_URL must be a bare origin")
        if len(self.worker_token) < 32:
            raise ValueError("WORKER_TOKEN must have at least 32 characters")
        if self.mode == "development":
            if u.scheme != "http" or u.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("Development credentials are loopback only")
            if len(self.dev_mcp_token) < 32 or self.dev_mcp_token == self.worker_token:
                raise ValueError("Separate development MCP token required (32+ characters)")
        elif self.mode == "production":
            values = (self.worker_token, self.owner_subject, self.issuer, self.audience)
            if any(v.startswith(("REPLACE_", "GENERATE_")) for v in values):
                raise ValueError("Production placeholders must be replaced")
            if u.scheme != "https" or not u.hostname:
                raise ValueError("Production PUBLIC_URL requires HTTPS")
            if not all((self.issuer, self.jwks_url, self.owner_subject, self.audience)):
                raise ValueError(
                    "Production requires OAuth issuer, JWKS URL, audience and owner subject"
                )
            if any(urlsplit(v).scheme != "https" for v in (self.issuer, self.jwks_url)):
                raise ValueError("OAuth endpoints require HTTPS")
        else:
            raise ValueError("MODE must be production or development")


@dataclass
class WorkerConfig:
    url: str = field(default_factory=lambda: env("SERVER_URL", "http://127.0.0.1:8000"))
    token: str = field(default_factory=lambda: env("WORKER_TOKEN"))
    worker_id: str = field(default_factory=lambda: env("WORKER_ID", "home"))
    workspaces: dict = field(default_factory=lambda: json.loads(env("WORKSPACE_MAP", "{}")))
    state: str = field(default_factory=lambda: env("WORKER_STATE", "private/worker.sqlite"))
    codex: str = field(default_factory=lambda: env("CODEX_BIN", "codex"))
    model: str = field(default_factory=lambda: env("CODEX_MODEL"))
    sandbox: str = field(default_factory=lambda: env("SANDBOX", "workspace-write"))
    poll: float = field(default_factory=lambda: float(env("POLL_SECONDS", "2")))
    approval_timeout: int = 300

    def validate(self):
        u = urlsplit(self.url)
        if u.scheme != "https" and not (
            u.scheme == "http" and u.hostname in ("localhost", "127.0.0.1", "::1")
        ):
            raise ValueError("Worker requires HTTPS except on loopback")
        if len(self.token) < 32 or not self.workspaces or self.poll < 0.1:
            raise ValueError("Worker token, workspace map and valid poll interval required")
        if self.sandbox not in ("read-only", "workspace-write"):
            raise ValueError("Only read-only or workspace-write sandbox supported")
        self.workspaces = {k: str(self.resolve_workspace(k)) for k in self.workspaces}

    def resolve_workspace(self, workspace):
        if workspace not in self.workspaces:
            raise ValueError("Unauthorized workspace")
        p = Path(self.workspaces[workspace])
        if not p.is_absolute() or p.is_symlink() or not p.is_dir():
            raise ValueError(
                "Workspace must be an existing absolute directory without symlink aliases"
            )
        resolved = p.resolve(strict=True)
        if str(p) != str(resolved):
            raise ValueError("Workspace path must be canonical (no symlink parents or traversal)")
        return resolved
