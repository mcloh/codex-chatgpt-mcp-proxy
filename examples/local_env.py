"""Generate isolated loopback credentials. Run from the repository root."""

import os
import secrets
from pathlib import Path

os.umask(0o077)
root = Path.cwd().resolve()
private = root / "private"
private.mkdir(mode=0o700, exist_ok=True)
file = private / "local.env"
if file.exists():
    raise SystemExit("Refusing to overwrite existing local environment")
(root / "demo").mkdir(exist_ok=True)
worker = secrets.token_urlsafe(48)
mcp = secrets.token_urlsafe(48)
file.write_text(
    "\n".join(
        [
            "BRIDGE_MODE=development",
            "BRIDGE_PUBLIC_URL=http://127.0.0.1:8000",
            "BRIDGE_SERVER_URL=http://127.0.0.1:8000",
            "BRIDGE_WORKER_TOKEN=" + worker,
            "BRIDGE_DEV_MCP_TOKEN=" + mcp,
            "BRIDGE_DATABASE=private/server.sqlite",
            "BRIDGE_WORKER_STATE=private/worker.sqlite",
            "BRIDGE_WORKSPACES=demo",
            'BRIDGE_WORKSPACE_MAP=\'{"demo":"' + str(root / "demo") + "\"}'",
            "BRIDGE_CODEX_MODEL=gpt-6-luna",
            "BRIDGE_POLL_SECONDS=2",
            "",
        ]
    )
)
os.chmod(file, 0o600)
print("Generated private/local.env; tokens are not printed")
