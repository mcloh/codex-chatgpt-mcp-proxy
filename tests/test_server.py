from fastapi.testclient import TestClient
from mcp.client.streamable_http import streamable_http_client
from codex_chatgpt_mcp_proxy.server import create_app


def test_api_auth_host_origin_and_isolation(config):
    with TestClient(create_app(config), base_url=config.public_url) as c:
        assert c.get("/healthz").json() == {"ok": True}
        assert c.get("/healthz", headers={"host": "evil.example"}).status_code == 403
        assert c.get("/healthz", headers={"origin": "https://evil.example"}).status_code == 403
        for token in (None, "invalid", config.dev_mcp_token):
            h = {"Authorization": "Bearer " + token} if token else {}
            assert c.post("/api/worker/claim", headers=h).status_code == 401
        assert (
            c.post("/mcp", headers={"Authorization": "Bearer " + config.worker_token}).status_code
            == 401
        )
        h = {"Authorization": "Bearer " + config.worker_token}
        assert (
            c.post(
                "/api/worker/register",
                headers=h,
                json={
                    "worker_id": "home",
                    "protocol": "1",
                    "version": "test",
                    "workspaces": ["demo"],
                },
            ).status_code
            == 200
        )
        assert c.post("/api/worker/claim", headers=h).json() == {"task": None}
        assert (
            c.post(
                "/api/worker/register",
                headers=h,
                json={
                    "worker_id": "home",
                    "protocol": "1",
                    "version": "test",
                    "workspaces": ["/tmp"],
                },
            ).status_code
            == 400
        )
        assert c.post("/api/worker/claim", headers=h, content="x" * 140000).status_code == 413


async def test_real_mcp_transport_initialize_discover_and_validation(config):
    import httpx2

    app = create_app(config)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            headers={"Authorization": "Bearer " + config.dev_mcp_token},
        ) as http:
            async with streamable_http_client(config.public_url + "/mcp", http_client=http) as (
                read,
                write,
            ):
                from mcp import ClientSession

                async with ClientSession(read, write) as session:
                    initialization = await session.initialize()
                    assert initialization.server_info.name == "codex-chatgpt-mcp-proxy"
                    tools = await session.list_tools()
                    names = {t.name for t in tools.tools}
                    assert names == {
                        "bridge_status",
                        "submit_task",
                        "get_task",
                        "get_task_events",
                        "continue_task",
                        "cancel_task",
                        "list_tasks",
                    }
                    invalid = await session.call_tool(
                        "submit_task",
                        {
                            "instruction": "x",
                            "workspace": "demo",
                            "idempotency_key": "k",
                            "timeout_seconds": 0,
                        },
                    )
                    assert invalid.is_error
                    result = await session.call_tool(
                        "submit_task",
                        {"instruction": "hello", "workspace": "demo", "idempotency_key": "k"},
                    )
                    assert not result.is_error
                    assert result.structured_content["state"] == "queued"
                    repeated = await session.call_tool(
                        "submit_task",
                        {"instruction": "hello", "workspace": "demo", "idempotency_key": "k"},
                    )
                    assert not repeated.is_error
                    assert repeated.structured_content["id"] == result.structured_content["id"]
                    for tool in tools.tools:
                        assert tool.annotations is not None
                        assert tool.meta["securitySchemes"][0]["type"] == "oauth2"
