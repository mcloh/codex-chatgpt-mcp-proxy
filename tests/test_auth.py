import time
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from codex_chatgpt_mcp_proxy.auth import OAuthVerifier, AuthError
from codex_chatgpt_mcp_proxy.config import ServerConfig


async def test_oauth_signature_issuer_audience_owner_scope_and_expiry():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    config = ServerConfig(
        mode="production",
        public_url="https://bridge.example.com",
        worker_token="w" * 40,
        issuer="https://identity.example.com/",
        jwks_url="https://identity.example.com/jwks",
        audience="https://bridge.example.com",
        owner_subject="owner",
    )
    config.validate()
    verifier = OAuthVerifier(config)
    verifier.keys = {"test": key.public_key()}
    verifier.expires = time.time() + 300
    claims = {
        "iss": config.issuer,
        "aud": config.audience,
        "sub": "owner",
        "scope": "bridge:access",
        "iat": int(time.time()),
        "exp": int(time.time()) + 60,
    }

    def token(c):
        return jwt.encode(c, key, algorithm="RS256", headers={"kid": "test"})

    await verifier.verify(token(claims))
    for changed in (
        {"scope": "other"},
        {"sub": "intruder"},
        {"aud": "different"},
        {"iss": "different"},
        {"exp": int(time.time()) - 60},
    ):
        with pytest.raises(AuthError):
            await verifier.verify(token({**claims, **changed}))
    with pytest.raises(AuthError):
        await verifier.verify("garbage")
    with pytest.raises(ValueError):
        ServerConfig(worker_token="w" * 40).validate()
    with pytest.raises(ValueError):
        ServerConfig(
            mode="development",
            public_url="https://bridge.example.com",
            worker_token="w" * 40,
            dev_mcp_token="m" * 40,
        ).validate()
