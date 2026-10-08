"""Shapes of the FortiFlex v2 objects this library reads.

``total=False``: FortiFlex omits fields freely (a PENDING entitlement has no
``token``; older configs carry ``configName`` instead of ``name``), so every key is
optional and readers use ``.get``. Unknown extra keys pass through untouched.
"""

from __future__ import annotations

from typing import Any, TypedDict


class Program(TypedDict, total=False):
    serialNumber: str
    accountId: int
    startDate: str
    endDate: str
    hasSupportCoverage: bool


class ProductType(TypedDict, total=False):
    id: int
    name: str


class Config(TypedDict, total=False):
    id: int
    configId: int
    name: str
    configName: str
    accountId: int
    programSerialNumber: str
    status: str
    productType: ProductType
    parameters: list[dict[str, Any]]


class Entitlement(TypedDict, total=False):
    serialNumber: str
    configId: int
    accountId: int
    description: str
    status: str
    tokenStatus: str
    token: str
    startDate: str
    endDate: str


class Group(TypedDict, total=False):
    folderPath: str
    availableTokens: int
    usedTokens: int
