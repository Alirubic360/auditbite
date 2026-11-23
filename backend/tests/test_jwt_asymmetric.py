import time, uuid
import jwt, pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from auditbite import api as api_mod
from auditbite.api import Deps, create_app
from auditbite.mailer import OutboxSender
from conftest import token

BASE = "https://proj.supabase.co"


class FakeJWKS:
    def __init__(self, pub): self.pub = pub
    def get_signing_key_from_jwt(self, t): return type("K", (), {"key": self.pub})()


@pytest.fixture
def client(dburl, storage, monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    monkeypatch.setenv("SUPABASE_URL", BASE)
    monkeypatch.setattr(api_mod, "jwks_client", lambda base: FakeJWKS(key.public_key()))
    return TestClient(create_app(Deps(storage, None, OutboxSender()))), key


def es(key, **over):
    claims = {"sub": str(uuid.uuid4()), "aud": "authenticated", "iss": f"{BASE}/auth/v1", "exp": int(time.time()) + 600, **over}
    return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "k1"})


def test_es256_token_accepted(client):
    c, key = client
    assert c.post("/onboarding", json={"org_name": "X"}, headers={"Authorization": f"Bearer {es(key)}"}).status_code == 201


def test_es256_rejections(client):
    c, key = client
    other = ec.generate_private_key(ec.SECP256R1())
    for bad in (es(other), es(key, iss="https://evil.example/auth/v1"), es(key, aud="anon"), es(key, exp=int(time.time()) - 10)):
        assert c.get("/dashboard", headers={"Authorization": f"Bearer {bad}"}).status_code == 401


def test_alg_none_and_unknown_alg_rejected(client):
    c, key = client
    none = jwt.encode({"sub": str(uuid.uuid4()), "aud": "authenticated"}, None, algorithm="none")
    assert c.get("/dashboard", headers={"Authorization": f"Bearer {none}"}).status_code == 401
    assert c.get("/dashboard", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_hs256_still_works(client):
    c, _ = client
    assert c.post("/onboarding", json={"org_name": "H"}, headers={"Authorization": f"Bearer {token(uuid.uuid4())}"}).status_code == 201
