"""Match FortiFlex configs to Fortinet product types.

A config's ``productType.name`` is a family plus a form factor ("FortiGate-VM",
"FortiSOAR-VM"). Product short codes map to the family, matched as a
case-insensitive substring, so an FGT accepts "FortiGate-VM" and
"FortiGate-Hardware" but a FortiSOAR config is caught before it is wired to an
FGT (Fabric Studio answers that with an opaque 500).
"""

from __future__ import annotations

from .client import FlexError, FortiFlexClient, config_id_of
from .types import Config

PRODUCT_FAMILIES = {
    "FGT": "FortiGate",
    "FMG": "FortiManager",
    "FAZ": "FortiAnalyzer",
    "FML": "FortiMail",
    "FWB": "FortiWeb",
    "FAC": "FortiAuthenticator",
    "FAD": "FortiADC",
    "FPX": "FortiProxy",
    "SOAR": "FortiSOAR",
    "FSR": "FortiSOAR",
}


def family_for(product: str) -> str | None:
    return PRODUCT_FAMILIES.get(product.upper())


def config_family(cfg: Config) -> str:
    return str((cfg.get("productType") or {}).get("name") or "")


def _label(cfg: Config) -> str:
    return f"{cfg.get('id')} ({cfg.get('name') or cfg.get('configName')!r})"


def single_active_config(client: FortiFlexClient, product: str) -> int:
    """The account's one ACTIVE config for ``product``. Raises on zero or several
    rather than guessing, so nothing is billed against the wrong config."""
    want = family_for(product)
    if not want:
        raise FlexError(f"no FortiFlex product family known for {product!r}")
    cands = [
        c
        for c in client.configs()
        if want.lower() in config_family(c).lower()
        and str(c.get("status") or "").upper() == "ACTIVE"
    ]
    if not cands:
        raise FlexError(
            f"{product.upper()} needs a FortiFlex config but this account has no ACTIVE {want}-* config"
        )
    if len(cands) > 1:
        raise FlexError(
            f"{product.upper()} has {len(cands)} ACTIVE {want}-* configs, so the choice is "
            f"ambiguous: {', '.join(_label(c) for c in cands)}"
        )
    return config_id_of(cands[0])


def config_matches(cfg: Config, product: str) -> bool:
    """True when ``cfg``'s product family fits ``product`` (or ``product`` is unmapped)."""
    want = family_for(product)
    return not want or want.lower() in config_family(cfg).lower()
