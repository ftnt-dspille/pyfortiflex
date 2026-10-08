"""Live, READ-ONLY checks against the real FortiFlex API.

Skipped unless ``FORTIFLEX_LIVE=1`` and ``FORTIFLEX_USER`` / ``FORTIFLEX_PASS`` are set:

    FORTIFLEX_LIVE=1 FORTIFLEX_USER=... FORTIFLEX_PASS=... pytest tests/live -v

Nothing here changes the account. Deliberately NOT covered live:
  - ``regenerate_token`` / ``mint_token``: a new token invalidates the old one,
    which can break a VM that has not redeemed it yet;
  - creating entitlements or configs: consumes points;
  - stop / reactivate / transfer / update: changes who holds a license.
Those are covered offline (tests/test_*.py) against recorded request shapes.
"""

from __future__ import annotations

import os

import pytest

from pyfortiflex import FortiFlexClient, config_id_of, credentials_from_env, is_owned, token_used

pytestmark = pytest.mark.skipif(
    os.environ.get("FORTIFLEX_LIVE") != "1" or credentials_from_env() is None,
    reason="live FortiFlex tests need FORTIFLEX_LIVE=1 and FORTIFLEX_USER/FORTIFLEX_PASS",
)


@pytest.fixture(scope="module")
def flex() -> FortiFlexClient:
    return FortiFlexClient.from_env()


@pytest.fixture(scope="module")
def configs(flex: FortiFlexClient) -> list:
    found = flex.configs()
    if not found:
        pytest.skip("account has no FortiFlex configs")
    return found


def test_authenticates_and_lists_programs(flex: FortiFlexClient) -> None:
    programs = flex.programs()
    assert programs, "an IAM API user with FortiFlex access should see at least one program"
    assert all(p.get("serialNumber") for p in programs)


def test_configs_have_ids_names_and_product_types(configs: list) -> None:
    for cfg in configs:
        assert config_id_of(cfg) > 0
        assert cfg.get("name") or cfg.get("configName")
        assert (cfg.get("productType") or {}).get("name")


def test_config_lookup_by_id_and_name_round_trips(flex: FortiFlexClient, configs: list) -> None:
    cfg = configs[0]
    cid = config_id_of(cfg)
    assert config_id_of(flex.config(cid) or {}) == cid
    names = [str(c.get("name") or c.get("configName")).lower() for c in configs]
    name = str(cfg.get("name") or cfg.get("configName"))
    if names.count(name.lower()) == 1:
        assert flex.resolve_config_id(name) == cid


def test_entitlements_have_the_fields_allocation_reads(
    flex: FortiFlexClient, configs: list
) -> None:
    """mint_token decides on serialNumber, status, description and tokenStatus.
    If FortiFlex renames one, allocation would silently misjudge ownership."""
    for cfg in configs:
        ents = flex.entitlements(config_id=config_id_of(cfg))
        if ents:
            break
    else:
        pytest.skip("no config has entitlements")
    for ent in ents:
        assert ent.get("serialNumber")
        assert ent.get("status") in {"ACTIVE", "PENDING", "STOPPED", "EXPIRED"}, ent.get("status")
        assert "description" in ent
        assert ent.get("tokenStatus") in {"USED", "NOTUSED", None}, ent.get("tokenStatus")
        is_owned(ent), token_used(ent)  # must not raise on real payloads


def test_groups_list(flex: FortiFlexClient, configs: list) -> None:
    account_id = configs[0].get("accountId")
    assert account_id, "configs carry accountId; groups/list requires it"
    assert isinstance(flex.groups(account_id), list)
