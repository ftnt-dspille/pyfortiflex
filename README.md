# pyfortiflex

Python client for the **FortiFlex v2 API**, plus safe per-node entitlement
allocation for labs that license many VMs from one config.

```python
from pyfortiflex import FortiFlexClient, mint_token, owner_description

flex = FortiFlexClient.from_env()          # FORTIFLEX_USER / FORTIFLEX_PASS (IAM API user)
owner = owner_description("soar-801-validate", "soar801", "fabric-pod.example.com")
result = mint_token(flex, 12345, owner)    # config id or name
result.serial_number, result.token         # redeem the token on the VM
```

## Why allocation needs care

A FortiFlex entitlement is single-occupancy: the VM that redeems its token owns
the serial. Hand two VMs the same serial and the second is blocked (FortiSOAR
reports `FSR-Auth-018: License is detected as duplicate`).

`mint_token` records ownership in the entitlement's description
(`fabric <fabric> node <node> pod <pod>`) and allocates in this order:

1. the entitlement this owner already holds, so a re-run keeps its serial;
2. this owner's stopped entitlement, reactivated;
3. an active entitlement nobody owns **and whose token was never redeemed**;
4. an unowned stopped entitlement, reactivated;
5. a new entitlement (it can take ~30 min to turn active).

Tokens are single-use and consumed even by a failed redeem, so mint once per
redeem attempt. Repeat calls reuse the same serial.

## Client coverage

Programs; configs (list, get, resolve by name, create, update, enable, disable);
entitlements (list, create VM / hardware, update, stop, reactivate, transfer,
regenerate token, points); groups (list, next token). Product-family helpers
(`single_active_config`, `config_matches`) pick or check a config for a product
code such as `FGT` or `SOAR`.

Applying a token to a device (FortiGate, FortiManager, FortiSOAR) is the
consumer's job: pyfsr's `system.deploy_flex_license` for FortiSOAR,
fabric_studio_core for Fabric Studio VMs.

## Tests

```bash
uv venv && uv pip install -e ".[test,typecheck]"
.venv/bin/python -m pytest          # offline: every endpoint + allocation rule, 100% line coverage
.venv/bin/mypy src                  # strict
```

Live checks against the real API are opt-in and **read-only** (programs, configs,
entitlements, groups -- they verify the payload fields allocation depends on):

```bash
FORTIFLEX_LIVE=1 FORTIFLEX_USER=... FORTIFLEX_PASS=... .venv/bin/python -m pytest tests/live -v
```

Token regeneration, entitlement creation and stop/reactivate/transfer are never
run live: they invalidate tokens, consume points or move licenses.
