"""Client transport and config helpers -- offline, with requests.post stubbed."""

from __future__ import annotations

import pytest
import requests

from pyfortiflex import FlexError, FortiFlexClient, config_matches, single_active_config


class Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body
        self.text = str(body)
        self.content = b"x"

    def json(self):
        return self._body


@pytest.fixture
def calls(monkeypatch):
    log: list[tuple[str, dict]] = []
    queue: list[Resp] = []

    def post(url, **kw):
        log.append((url, kw.get("json")))
        if "oauth/token" in url:
            return Resp(200, {"access_token": "AT"})
        return queue.pop(0)

    monkeypatch.setattr(requests, "post", post)
    return log, queue


def test_reauthenticates_once_on_401(calls):
    log, queue = calls
    queue += [Resp(401, {}), Resp(200, {"programs": [{"serialNumber": "P1"}]})]
    c = FortiFlexClient("id", "pw")
    assert c.programs() == [{"serialNumber": "P1"}]
    assert sum("oauth/token" in u for u, _ in log) == 2


def test_error_status_raises(calls):
    _, queue = calls
    queue.append(Resp(400, {"message": "bad"}))
    with pytest.raises(FlexError, match="entitlements/list failed \\(400\\)"):
        FortiFlexClient("id", "pw").entitlements(config_id=1)


def test_configs_walks_every_program(calls):
    log, queue = calls
    queue += [
        Resp(200, {"programs": [{"serialNumber": "P1"}, {"serialNumber": "P2"}]}),
        Resp(200, {"configs": [{"id": 1}]}),
        Resp(200, {"configs": [{"id": 2}]}),
    ]
    assert [c["id"] for c in FortiFlexClient("id", "pw").configs()] == [1, 2]
    assert [j for u, j in log if u.endswith("configs/list")] == [
        {"programSerialNumber": "P1"},
        {"programSerialNumber": "P2"},
    ]


def test_resolve_config_id_passthrough_needs_no_api(calls):
    log, _ = calls
    c = FortiFlexClient("id", "pw")
    assert c.resolve_config_id(12345) == 12345 and c.resolve_config_id("12345") == 12345
    assert log == []


def test_resolve_config_id_by_name(calls):
    _, queue = calls
    queue += [
        Resp(200, {"programs": [{"serialNumber": "P1"}]}),
        Resp(200, {"configs": [{"id": 7, "name": "SOAR Lab"}, {"id": 8, "name": "FGT"}]}),
    ]
    assert FortiFlexClient("id", "pw").resolve_config_id("soar lab") == 7


def test_regenerate_token_requires_a_token(calls):
    _, queue = calls
    queue.append(Resp(200, {"entitlements": [{}]}))
    with pytest.raises(FlexError, match="no token"):
        FortiFlexClient("id", "pw").regenerate_token("SN1")


def test_single_active_config_refuses_to_guess(calls):
    _, queue = calls
    two = {
        "configs": [
            {
                "id": 1,
                "name": "a",
                "status": "ACTIVE",
                "productType": {"name": "FortiSOAR-VM"},
            },
            {
                "id": 2,
                "name": "b",
                "status": "ACTIVE",
                "productType": {"name": "FortiSOAR-VM"},
            },
        ]
    }
    queue += [Resp(200, {"programs": [{"serialNumber": "P1"}]}), Resp(200, two)]
    with pytest.raises(FlexError, match="ambiguous"):
        single_active_config(FortiFlexClient("id", "pw"), "SOAR")


def test_config_matches_family():
    soar = {"productType": {"name": "FortiSOAR-VM"}}
    assert config_matches(soar, "SOAR") and not config_matches(soar, "FGT")
    assert config_matches(soar, "UNKNOWN")


def test_single_active_config_happy_path_and_errors(monkeypatch):
    c = FortiFlexClient("id", "pw")
    soar = {
        "id": 3,
        "name": "s",
        "status": "ACTIVE",
        "productType": {"name": "FortiSOAR-VM"},
    }
    stopped = {
        "id": 4,
        "name": "t",
        "status": "DISABLED",
        "productType": {"name": "FortiSOAR-VM"},
    }
    monkeypatch.setattr(c, "configs", lambda program_serial=None: [soar, stopped])
    assert single_active_config(c, "soar") == 3
    with pytest.raises(FlexError, match="no ACTIVE FortiGate"):
        single_active_config(c, "FGT")
    with pytest.raises(FlexError, match="no FortiFlex product family"):
        single_active_config(c, "XYZ")
