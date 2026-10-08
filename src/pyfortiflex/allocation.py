"""Hand each VM its own FortiFlex serial and mint it a fresh token.

A FortiFlex entitlement is single-occupancy: the VM that redeems its token owns
the serial. A config serves a whole fleet, so "pick the ACTIVE one" is wrong --
two nodes would get the same serial and the second one is blocked (FortiSOAR's
FSR-Auth-018 "License is detected as duplicate"). Ownership is recorded in the
entitlement's ``description``; :func:`owner_description` is the key.

Allocation order in :func:`mint_token`:
  1. the ACTIVE entitlement this owner already holds (description match) -- a
     re-run keeps its serial;
  2. this owner's STOPPED entitlement (a destroy stops them), reactivated;
  3. an ACTIVE entitlement nobody owns: description not in owner form AND token
     never redeemed (a USED token means some VM holds it -- FS's ``API:<uuid>``
     ones and hand-labelled ones are live on other boxes);
  4. an unowned STOPPED one, reactivated (stopped = no VM holds it);
  5. create one and wait for it to turn ACTIVE (~30 min).
Steps 3 and 4 write the owner's description back so the claim survives the run.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

from .client import FlexError, FortiFlexClient
from .types import Entitlement

# "fabric X node Y pod Z"; older runs wrote it without the pod.
OWNER_RX = re.compile(r"^fabric \S+ node \S+( pod \S+)?$")

# Serials handed out in THIS process, per config. FortiFlex has no reserve call,
# and concurrent mints would otherwise both take the same unowned entitlement.
_RESERVED: dict[str, set[str]] = {}
_RESERVED_LOCK = threading.Lock()


def owner_description(fabric: str, node: str, pod: str) -> str:
    """The description that owns one node's serial. Keyed by pod as well as
    fabric + node: the same topology on two pods is two VMs."""
    return f"fabric {fabric} node {node} pod {pod}"


def is_owned(ent: Entitlement) -> bool:
    return bool(OWNER_RX.match(_desc(ent)))


def token_used(ent: Entitlement) -> bool:
    return str(ent.get("tokenStatus") or "").upper() == "USED"


def _serial(ent: Entitlement) -> str:
    return str(ent.get("serialNumber") or ent.get("serial") or "").strip()


def _desc(ent: Entitlement) -> str:
    return str(ent.get("description") or "").strip()


def _status(ent: Entitlement) -> str:
    return str(ent.get("status") or "").upper()


def reset_reservations() -> None:
    """Forget this process's reservations (tests; a long-lived worker between jobs)."""
    with _RESERVED_LOCK:
        _RESERVED.clear()


@dataclass
class MintResult:
    config_id: int
    serial_number: str
    token: str
    created: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "serial_number": self.serial_number,
            "token": self.token,
            "created": self.created,
        }


def _claim(
    client: FortiFlexClient,
    config_id: int,
    ent: Entitlement,
    owner: str,
    say: Callable[[str], None],
) -> None:
    serial = _serial(ent)
    try:
        client.post(
            "entitlements/update",
            {"configId": config_id, "serialNumber": serial, "description": owner},
        )
        say(f"mint: claimed entitlement {serial} for {owner!r}")
    except Exception as e:  # noqa: BLE001 -- the in-process reservation still holds
        say(
            f"mint: claimed {serial} in-process but could NOT record ownership "
            f"in FortiFlex ({e}) -- a later run may hand it to another node"
        )


def _reactivate(client: FortiFlexClient, ent: Entitlement, say: Callable[[str], None]) -> bool:
    try:
        client.reactivate_entitlement(_serial(ent))
        say(f"mint: reactivated STOPPED entitlement {_serial(ent)}")
        return True
    except Exception as e:  # noqa: BLE001
        say(f"mint: could not reactivate {_serial(ent)} ({e})")
        return False


def _take_unowned(config_id: int, pool: list[Entitlement]) -> Entitlement | None:
    with _RESERVED_LOCK:
        reserved = _RESERVED.setdefault(str(config_id), set())
        pick = next((e for e in pool if _serial(e) not in reserved), None)
        if pick is not None:
            reserved.add(_serial(pick))
        return pick


def mint_token(
    client: FortiFlexClient,
    config: int | str,
    owner: str,
    *,
    timeout_s: int = 1800,
    poll_interval_s: int = 30,
    progress: Callable[[str], None] | None = None,
) -> MintResult:
    """Allocate ``owner``'s serial on ``config`` (id or name) and regenerate its
    token. Tokens are single-use and consumed even by a failed redeem, so call
    this once per redeem attempt; repeat calls reuse the same serial.

    ``timeout_s`` bounds the wait for a created entitlement to turn ACTIVE."""
    say = progress or (lambda _m: None)
    config_id = client.resolve_config_id(config)
    deadline = time.time() + timeout_s
    created = False
    selected: Entitlement | None = None

    while time.time() < deadline:
        ents = client.entitlements(config_id=config_id)
        active = sorted((e for e in ents if _status(e) == "ACTIVE"), key=_serial)
        stopped = sorted((e for e in ents if _status(e) == "STOPPED"), key=_serial)

        mine = [e for e in active if _desc(e) == owner]
        if mine:
            selected = mine[0]
            say(f"mint: reusing this owner's entitlement {_serial(selected)}")
            break

        mine_stopped = [e for e in stopped if _desc(e) == owner]
        if mine_stopped and _reactivate(client, mine_stopped[0], say):
            selected = mine_stopped[0]
            break

        selected = _take_unowned(
            config_id, [e for e in active if not is_owned(e) and not token_used(e)]
        )
        if selected is not None:
            _claim(client, config_id, selected, owner, say)
            break

        candidate = _take_unowned(config_id, [e for e in stopped if not is_owned(e)])
        if candidate is not None and _reactivate(client, candidate, say):
            selected = candidate
            _claim(client, config_id, selected, owner, say)
            break

        if not created:
            say(
                f"mint: no free entitlement on config {config_id} "
                f"({len(active)} ACTIVE, all owned or in use); creating one"
            )
            client.create_vm_entitlements(config_id, description=owner)
            created = True
        say(f"mint: waiting for entitlement to go ACTIVE; {int(deadline - time.time())}s left")
        time.sleep(poll_interval_s)

    if selected is None:
        raise FlexError(f"no ACTIVE entitlement for config {config_id} within {timeout_s}s")
    serial = _serial(selected)
    if not serial:
        raise FlexError(f"selected entitlement has no serialNumber: {selected}")
    token = client.regenerate_token(serial)
    say(f"mint: regenerated token for {serial}")
    return MintResult(config_id=config_id, serial_number=serial, token=token, created=created)
