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
from .products import PRODUCT_FAMILIES, config_matches, family_for, single_active_config
from .types import Config, Entitlement, Group, ProductType, Program

__all__ = [
    "API_URL",
    "AUTH_URL",
    "PRODUCT_FAMILIES",
    "Config",
    "Entitlement",
    "FlexError",
    "FortiFlexClient",
    "Group",
    "MintResult",
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
