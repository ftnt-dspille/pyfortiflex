"""pyfortiflex -- FortiFlex v2 API client and per-node entitlement allocation."""

from .allocation import (
    MintResult,
    is_owned,
    mint_token,
    owner_description,
    reset_reservations,
    token_used,
)
from .client import (
    API_URL,
    AUTH_URL,
    FlexError,
    FortiFlexClient,
    config_id_of,
    credentials_from_env,
)
from .types import Config, Entitlement, Group, Program, ProductType
from .products import PRODUCT_FAMILIES, config_matches, family_for, single_active_config

__all__ = [
    "API_URL",
    "AUTH_URL",
    "Config",
    "Entitlement",
    "FlexError",
    "FortiFlexClient",
    "Group",
    "MintResult",
    "PRODUCT_FAMILIES",
    "ProductType",
    "Program",
    "config_id_of",
    "config_matches",
    "credentials_from_env",
    "family_for",
    "is_owned",
    "mint_token",
    "owner_description",
    "reset_reservations",
    "single_active_config",
    "token_used",
]
