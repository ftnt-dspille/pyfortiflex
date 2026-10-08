"""Allocation rules -- offline, against a fake FortiFlex client."""

from __future__ import annotations

from typing import Any

import pytest

import pyfortiflex.allocation as alloc
from pyfortiflex import FlexError, mint_token, owner_description


class FakeFlex:
    """Records calls; serves a mutable entitlement list."""

    def __init__(self, entitlements: list[dict[str, Any]], *, activate_after: int | None = None):
        self.ents = entitlements
        self.calls: list[tuple[str, Any]] = []
        self.activate_after = activate_after
        self._lists = 0

    def resolve_config_id(self, config):
        return int(config)

    def entitlements(self, *, config_id):
        self._lists += 1
        if self.activate_after is not None and self._lists > self.activate_after:
            for e in self.ents:
                if e["status"] == "PENDING":
                    e["status"] = "ACTIVE"
        return [dict(e) for e in self.ents]

    def post(self, path, payload=None):
        self.calls.append((path, payload))
        return {}

    def reactivate_entitlement(self, serial):
        self.calls.append(("reactivate", serial))

    def create_vm_entitlements(self, config_id, *, description=""):
        self.calls.append(("create", description))
        self.ents.append({"serialNumber": "NEW1", "status": "PENDING", "description": description})

    def regenerate_token(self, serial):
        self.calls.append(("token", serial))
        return f"TOKEN-{serial}"


@pytest.fixture(autouse=True)
def _reset():
    alloc.reset_reservations()
    yield
    alloc.reset_reservations()


OWNER = owner_description("soar-801-validate", "soar801", "10.0.0.1")


def test_owner_description_shape():
    assert OWNER == "fabric soar-801-validate node soar801 pod 10.0.0.1"
    assert alloc.OWNER_RX.match(OWNER)
    assert alloc.OWNER_RX.match("fabric f node n")  # pre-pod form still counts as owned


def test_reuses_own_serial_even_when_its_token_is_used():
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
            {
                "serialNumber": "SN9",
                "status": "ACTIVE",
                "description": OWNER,
                "tokenStatus": "USED",
            },
        ]
    )
    r = mint_token(fx, 1, OWNER, timeout_s=5)
    assert (r.serial_number, r.token, r.created) == ("SN9", "TOKEN-SN9", False)
    assert not any(c[0] == "entitlements/update" for c in fx.calls)


def test_never_takes_a_serial_whose_token_is_used():
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "API:ABC",
                "tokenStatus": "USED",
            },
            {
                "serialNumber": "SN2",
                "status": "ACTIVE",
                "description": "https://x:13000",
                "tokenStatus": "USED",
            },
            {
                "serialNumber": "SN3",
                "status": "ACTIVE",
                "description": "API:ABC",
                "tokenStatus": "NOTUSED",
            },
        ]
    )
    r = mint_token(fx, 1, OWNER, timeout_s=5)
    assert r.serial_number == "SN3"
    assert (
        "entitlements/update",
        {"configId": 1, "serialNumber": "SN3", "description": OWNER},
    ) in fx.calls


def test_same_node_on_another_pod_gets_its_own_serial():
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN9",
                "status": "ACTIVE",
                "description": owner_description("f", "n", "pod-a"),
                "tokenStatus": "USED",
            },
            {
                "serialNumber": "SN5",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
        ]
    )
    r = mint_token(fx, 1, owner_description("f", "n", "pod-b"), timeout_s=5)
    assert r.serial_number == "SN5"


def test_reactivates_own_stopped_serial_before_taking_another():
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
            {
                "serialNumber": "SN7",
                "status": "STOPPED",
                "description": OWNER,
                "tokenStatus": "USED",
            },
        ]
    )
    r = mint_token(fx, 1, OWNER, timeout_s=5)
    assert r.serial_number == "SN7"
    assert ("reactivate", "SN7") in fx.calls


def test_unowned_stopped_serial_is_reused_even_if_its_token_was_used():
    """STOPPED means no VM holds it, so a used token does not block reuse."""
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN4",
                "status": "STOPPED",
                "description": "API:X",
                "tokenStatus": "USED",
            },
        ]
    )
    r = mint_token(fx, 1, OWNER, timeout_s=5)
    assert r.serial_number == "SN4"
    assert ("reactivate", "SN4") in fx.calls


def test_two_owners_in_one_process_never_share_a_serial():
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
            {
                "serialNumber": "SN2",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
        ]
    )
    a = mint_token(fx, 1, owner_description("f", "a", "p"), timeout_s=5)
    b = mint_token(fx, 1, owner_description("f", "b", "p"), timeout_s=5)
    assert {a.serial_number, b.serial_number} == {"SN1", "SN2"}


def test_creates_and_waits_when_nothing_is_free(monkeypatch):
    monkeypatch.setattr(alloc.time, "sleep", lambda s: None)
    fx = FakeFlex(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "API:X",
                "tokenStatus": "USED",
            },
        ],
        activate_after=1,
    )
    lines: list[str] = []
    r = mint_token(fx, 1, OWNER, timeout_s=60, progress=lines.append)
    assert (r.serial_number, r.created) == ("NEW1", True)
    assert ("create", OWNER) in fx.calls
    assert any("waiting for entitlement to go ACTIVE" in m for m in lines)


def test_times_out_when_nothing_turns_active(monkeypatch):
    clock = iter(range(0, 10_000, 100))
    monkeypatch.setattr(alloc.time, "time", lambda: next(clock))
    monkeypatch.setattr(alloc.time, "sleep", lambda s: None)
    fx = FakeFlex([])
    with pytest.raises(FlexError, match="no ACTIVE entitlement"):
        mint_token(fx, 1, OWNER, timeout_s=250)


# ── failure branches ──────────────────────────────────────────────────────


class _FailingClaims(FakeFlex):
    def post(self, path, payload=None):
        raise RuntimeError("FortiFlex down")


def test_claim_write_failure_still_allocates_and_says_so():
    fx = _FailingClaims(
        [
            {
                "serialNumber": "SN1",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            }
        ]
    )
    lines: list[str] = []
    r = mint_token(fx, 1, OWNER, timeout_s=5, progress=lines.append)
    assert r.serial_number == "SN1"
    assert any("could NOT record ownership" in m for m in lines)


class _NoReactivate(FakeFlex):
    def reactivate_entitlement(self, serial):
        raise RuntimeError("refused")


def test_failed_reactivation_falls_through_to_a_free_serial():
    fx = _NoReactivate(
        [
            {"serialNumber": "SN7", "status": "STOPPED", "description": OWNER},
            {
                "serialNumber": "SN8",
                "status": "ACTIVE",
                "description": "",
                "tokenStatus": "NOTUSED",
            },
        ]
    )
    lines: list[str] = []
    assert mint_token(fx, 1, OWNER, timeout_s=5, progress=lines.append).serial_number == "SN8"
    assert any("could not reactivate SN7" in m for m in lines)


def test_entitlement_without_serial_is_an_error():
    fx = FakeFlex([{"status": "ACTIVE", "description": OWNER}])
    with pytest.raises(FlexError, match="no serialNumber"):
        mint_token(fx, 1, OWNER, timeout_s=5)


def test_mint_result_as_dict():
    fx = FakeFlex([{"serialNumber": "SN9", "status": "ACTIVE", "description": OWNER}])
    assert mint_token(fx, 1, OWNER, timeout_s=5).as_dict() == {
        "config_id": 1,
        "serial_number": "SN9",
        "token": "TOKEN-SN9",
        "created": False,
    }


def test_is_owned_and_token_used():
    assert alloc.is_owned({"description": OWNER}) and not alloc.is_owned({"description": "API:X"})
    assert alloc.token_used({"tokenStatus": "used"}) and not alloc.token_used({})
