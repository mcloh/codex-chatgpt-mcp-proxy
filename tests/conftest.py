import pytest
from codex_chatgpt_mcp_proxy.config import ServerConfig
from codex_chatgpt_mcp_proxy.store import Store


@pytest.fixture
def config(tmp_path):
    return ServerConfig(
        database=str(tmp_path / "server.sqlite"),
        mode="development",
        worker_token="w" * 40,
        dev_mcp_token="m" * 40,
    )


@pytest.fixture
def store(config):
    return Store(config)


def claim(store):
    store.register("home", "1", "test", ["demo"])
    return store.claim("home")
