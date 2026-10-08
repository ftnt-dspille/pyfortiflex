"""Every endpoint method sends the right path + body and unwraps the right key."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
import requests

from pyfortiflex import FlexError, FortiFlexClient, config_id_of, credentials_from_env


class Recorder:
    def __init__(self, envelope: dict[str, Any]):
        self.envelope = envelope
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __call__(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append((path, payload))
        return self.envelope


ENT = [{"serialNumber": "SN1"}]
CFG = {"id": 7, "name": "c"}

# (call, path, body, envelope, expected result)
CASES: list[tuple[Callable[[FortiFlexClient], Any], str, dict[str, Any], dict[str, Any], Any]] = [
    (
        lambda c: c.programs(),
        "programs/list",
        {},
        {"programs": [{"serialNumber": "P"}]},
        [{"serialNumber": "P"}],
    ),
    (
        lambda c: c.configs("P"),
        "configs/list",
        {"programSerialNumber": "P"},
        {"configs": [CFG]},
        [CFG],
    ),
    (
        lambda c: c.create_config("P", "c", 101, [{"id": 1, "value": "2"}]),
        "configs/create",
        {
            "programSerialNumber": "P",
            "name": "c",
            "productTypeId": 101,
            "parameters": [{"id": 1, "value": "2"}],
        },
        {"configs": CFG},
        CFG,
    ),
    (
        lambda c: c.update_config(7, name="n"),
        "configs/update",
        {"id": 7, "name": "n"},
        {"configs": CFG},
        CFG,
    ),
    (lambda c: c.enable_config(7), "configs/enable", {"id": 7}, {"configs": CFG}, CFG),
    (lambda c: c.disable_config(7), "configs/disable", {"id": 7}, {}, {}),
    (
        lambda c: c.entitlements(config_id=7, status="ACTIVE"),
        "entitlements/list",
        {"configId": 7, "status": "ACTIVE"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.entitlements(program_serial="P"),
        "entitlements/list",
        {"programSerialNumber": "P"},
        {"data": ENT},
        ENT,
    ),
    (
        lambda c: c.create_vm_entitlements(7, count=2, description="d"),
        "entitlements/vm/create",
        {
            "configId": 7,
            "count": 2,
            "description": "d",
            "endDate": None,
            "skipPending": True,
            "folderPath": "My Assets",
        },
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.create_hardware_entitlements(7, ["FGT1", "FGT2"], end_date="2030-01-01"),
        "entitlements/hardware/create",
        {"configId": 7, "serialNumbers": ["FGT1", "FGT2"], "endDate": "2030-01-01"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.update_entitlement("SN1", description="d"),
        "entitlements/update",
        {"serialNumber": "SN1", "description": "d"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.stop_entitlement("SN1"),
        "entitlements/stop",
        {"serialNumber": "SN1"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.reactivate_entitlement("SN1"),
        "entitlements/reactivate",
        {"serialNumber": "SN1"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.transfer_entitlements(["SN1"], account_id=5, program_serial="P"),
        "entitlements/transfer",
        {"serialNumbers": ["SN1"], "accountId": 5, "programSerialNumber": "P"},
        {"entitlements": ENT},
        ENT,
    ),
    (
        lambda c: c.regenerate_token("SN1"),
        "entitlements/vm/token",
        {"serialNumber": "SN1"},
        {"entitlements": [{"token": "ABC"}]},
        "ABC",
    ),
    (
        lambda c: c.points("P", "2030-01-01", "2030-01-31"),
        "entitlements/points",
        {
            "programSerialNumber": "P",
            "startDate": "2030-01-01",
            "endDate": "2030-01-31",
        },
        {"entitlements": [{"serialNumber": "SN1", "points": 3}]},
        [{"serialNumber": "SN1", "points": 3}],
    ),
    (
        lambda c: c.groups(5),
        "groups/list",
        {"accountId": 5},
        {"fortiCareAssetGroups": [{"folderPath": "f"}]},
        [{"folderPath": "f"}],
    ),
    (
        lambda c: c.group_next_token(folder_path="f"),
        "groups/nexttoken",
        {"folderPath": "f"},
        {"entitlements": ENT},
        ENT,
    ),
]


@pytest.mark.parametrize(
    ("call", "path", "body", "envelope", "expected"), CASES, ids=[c[1] for c in CASES]
)
def test_endpoint(monkeypatch, call, path, body, envelope, expected):
    client = FortiFlexClient("id", "pw")
    rec = Recorder(envelope)
    monkeypatch.setattr(client, "post", rec)
    assert call(client) == expected
    sent_path, sent_body = rec.calls[-1]
    assert sent_path == path
    assert (sent_body or {}) == body


def test_missing_list_key_yields_empty_list(monkeypatch):
    client = FortiFlexClient("id", "pw")
    monkeypatch.setattr(client, "post", Recorder({"entitlements": "not-a-list"}))
    assert client.stop_entitlement("SN1") == []


def test_config_lookup_by_id_accepts_legacy_config_id(monkeypatch):
    client = FortiFlexClient("id", "pw")
    monkeypatch.setattr(client, "configs", lambda program_serial=None: [{"configId": 9}, {"id": 7}])
    assert client.config("9") == {"configId": 9}
    assert client.config(8) is None


def test_resolve_config_id_rejects_unknown_and_ambiguous_names(monkeypatch):
    client = FortiFlexClient("id", "pw")
    monkeypatch.setattr(
        client,
        "configs",
        lambda program_serial=None: [
            {"id": 1, "name": "dup"},
            {"id": 2, "name": "DUP"},
        ],
    )
    with pytest.raises(FlexError, match="ambiguous"):
        client.resolve_config_id("dup")
    with pytest.raises(FlexError, match="not found; available: dup, DUP"):
        client.resolve_config_id("other")


def test_config_id_of_requires_an_id():
    assert config_id_of({"configId": 3}) == 3
    with pytest.raises(FlexError, match="no id"):
        config_id_of({"name": "x"})


# ── construction and auth ─────────────────────────────────────────────────


def test_requires_credentials():
    with pytest.raises(FlexError, match="IAM API id and password"):
        FortiFlexClient("", "pw")


def test_from_env(monkeypatch):
    monkeypatch.setenv("FORTIFLEX_USER", "u")
    monkeypatch.setenv("FORTIFLEX_PASS", "p")
    assert credentials_from_env() == ("u", "p")
    assert FortiFlexClient.from_env(timeout=5).timeout == 5
    monkeypatch.delenv("FORTIFLEX_PASS")
    assert credentials_from_env() is None
    with pytest.raises(FlexError, match="FORTIFLEX_USER"):
        FortiFlexClient.from_env()


class _Resp:
    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.content = b"x" if body is not None else b""

    def json(self) -> Any:
        return self._body


def test_auth_failure_and_missing_token(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(401, {"error": "bad"}))
    with pytest.raises(FlexError, match="auth failed \\(401\\)"):
        FortiFlexClient("id", "pw").authenticate()
    monkeypatch.setattr(requests, "post", lambda url, **kw: _Resp(200, {}))
    with pytest.raises(FlexError, match="no access_token"):
        FortiFlexClient("id", "pw").authenticate()


def test_gives_up_after_a_second_401(monkeypatch):
    def post(url, **kw):
        return _Resp(200, {"access_token": "AT"}) if "oauth" in url else _Resp(401, {})

    monkeypatch.setattr(requests, "post", post)
    with pytest.raises(FlexError, match="unauthorized after re-authenticating"):
        FortiFlexClient("id", "pw").programs()


def test_bare_list_and_empty_bodies(monkeypatch):
    bodies = iter(
        [
            _Resp(200, {"access_token": "AT"}),
            _Resp(200, [{"serialNumber": "S"}]),
            _Resp(200, None),
        ]
    )
    monkeypatch.setattr(requests, "post", lambda url, **kw: next(bodies))
    client = FortiFlexClient("id", "pw")
    assert client.post("entitlements/list") == {"data": [{"serialNumber": "S"}]}
    assert client.post("configs/enable") == {}


def test_sends_bearer_token_and_json(monkeypatch):
    seen: list[dict[str, Any]] = []

    def post(url, **kw):
        seen.append({"url": url, **kw})
        return (
            _Resp(200, {"access_token": "AT"}) if "oauth" in url else _Resp(200, {"programs": []})
        )

    monkeypatch.setattr(requests, "post", post)
    FortiFlexClient("id", "pw", api_url="https://flex.example.com/v2/", timeout=7).programs()
    auth, call = seen
    assert auth["json"] == {
        "username": "id",
        "password": "pw",
        "client_id": "flexvm",
        "grant_type": "password",
    }
    assert call["url"] == "https://flex.example.com/v2/programs/list"
    assert call["headers"]["Authorization"] == "Bearer AT"
    assert call["timeout"] == 7
