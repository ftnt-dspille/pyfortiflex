"""FortiFlex v2 REST client.

Every FortiFlex call is a ``POST`` with a JSON body to
``https://support.fortinet.com/ES/api/fortiflex/v2/<path>``, authenticated with a
bearer token from the FortiCloud IAM API (``customerapiauth.fortinet.com``,
``client_id=flexvm``). The token is fetched lazily and re-fetched once on a 401.

Credentials are an IAM API user (id + password). :func:`credentials_from_env`
reads ``FORTIFLEX_USER`` / ``FORTIFLEX_PASS``; callers with their own secret store
(Keychain, vault) pass the pair in directly.
"""

from __future__ import annotations

import os
from typing import Any, TypeVar, cast

import requests

from .types import Config, Entitlement, Group, Program

AUTH_URL = "https://customerapiauth.fortinet.com/api/v1/oauth/token/"
API_URL = "https://support.fortinet.com/ES/api/fortiflex/v2"

_T = TypeVar("_T")


class FlexError(RuntimeError):
    """A FortiFlex credential, API or allocation failure."""


def credentials_from_env() -> tuple[str, str] | None:
    """``(api_id, password)`` from ``FORTIFLEX_USER`` / ``FORTIFLEX_PASS``, or None."""
    api_id, password = (
        os.environ.get("FORTIFLEX_USER"),
        os.environ.get("FORTIFLEX_PASS"),
    )
    return (api_id, password) if api_id and password else None


def _drop_empty(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in payload.items() if v is not None and v != ""}


def _items(data: dict[str, Any], key: str, _type: type[_T]) -> list[_T]:
    """The list under ``key`` in a response envelope ([] when absent or not a list)."""
    value = data.get(key)
    return cast("list[_T]", value) if isinstance(value, list) else []


def config_id_of(cfg: Config) -> int:
    """A config's numeric id (``id``, or ``configId`` on older payloads)."""
    cid = cfg.get("id", cfg.get("configId"))
    if cid is None:
        raise FlexError(f"FortiFlex config has no id: {cfg}")
    return int(cid)


class FortiFlexClient:
    """Thin wrapper over the FortiFlex v2 endpoints.

    Methods return the response's payload (e.g. the ``entitlements`` list), not
    the envelope. Raises :class:`FlexError` on auth failure or a non-2xx.
    """

    def __init__(
        self,
        api_id: str,
        password: str,
        *,
        api_url: str = API_URL,
        auth_url: str = AUTH_URL,
        timeout: float = 30,
    ) -> None:
        if not api_id or not password:
            raise FlexError("FortiFlex needs an IAM API id and password")
        self.api_id = api_id
        self._password = password
        self.api_url = api_url.rstrip("/")
        self.auth_url = auth_url
        self.timeout = timeout
        self._token: str | None = None

    @classmethod
    def from_env(cls, **kwargs: Any) -> FortiFlexClient:
        creds = credentials_from_env()
        if not creds:
            raise FlexError("set FORTIFLEX_USER / FORTIFLEX_PASS")
        return cls(*creds, **kwargs)

    # ── transport ──────────────────────────────────────────────────────────
    def authenticate(self) -> str:
        r = requests.post(
            self.auth_url,
            json={
                "username": self.api_id,
                "password": self._password,
                "client_id": "flexvm",
                "grant_type": "password",
            },
            timeout=self.timeout,
        )
        if r.status_code >= 400:
            raise FlexError(f"FortiFlex auth failed ({r.status_code}): {r.text[:200]}")
        token = (r.json() or {}).get("access_token")
        if not token:
            raise FlexError(f"FortiFlex auth returned no access_token: {r.text[:160]}")
        self._token = str(token)
        return self._token

    def post(self, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """POST ``payload`` to ``<api_url>/<path>`` and return the JSON envelope."""
        url = f"{self.api_url}/{path.lstrip('/')}"
        for attempt in (1, 2):
            token = self._token or self.authenticate()
            r = requests.post(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=payload or {},
                timeout=self.timeout,
            )
            if r.status_code == 401:
                if attempt == 1:  # token expired mid-session: fetch a new one, retry once
                    self._token = None
                    continue
                raise FlexError(f"FortiFlex {path}: unauthorized after re-authenticating")
            if r.status_code >= 400:
                raise FlexError(f"FortiFlex {path} failed ({r.status_code}): {r.text[:300]}")
            data = r.json() if r.content else {}
            return data if isinstance(data, dict) else {"data": data}
        raise AssertionError("unreachable")  # pragma: no cover

    # ── programs and configs ───────────────────────────────────────────────
    def programs(self) -> list[Program]:
        return _items(self.post("programs/list"), "programs", Program)

    def configs(self, program_serial: str | None = None) -> list[Config]:
        """Configs of one program, or of every program when ``program_serial`` is
        None (``configs/list`` requires a program serial; a bare call 400s)."""
        serials = (
            [program_serial]
            if program_serial
            else [p["serialNumber"] for p in self.programs() if p.get("serialNumber")]
        )
        out: list[Config] = []
        for sn in serials:
            out.extend(
                _items(
                    self.post("configs/list", {"programSerialNumber": sn}),
                    "configs",
                    Config,
                )
            )
        return out

    def config(self, config_id: int | str) -> Config | None:
        """One config by id (``configs/list`` has no id filter, so enumerate)."""
        cid = int(config_id)
        return next((c for c in self.configs() if c.get("id", c.get("configId")) == cid), None)

    def resolve_config_id(self, name_or_id: int | str) -> int:
        """A numeric id passes through with no API call; a name is matched
        case-insensitively. Raises on an unknown or ambiguous name."""
        if isinstance(name_or_id, int):
            return name_or_id
        s = str(name_or_id).strip()
        if s.isdigit():
            return int(s)
        configs = self.configs()
        matches = [
            c
            for c in configs
            if str(c.get("name") or c.get("configName") or "").lower() == s.lower()
        ]
        if not matches:
            names = (
                ", ".join(str(c.get("name") or c.get("configName")) for c in configs) or "(none)"
            )
            raise FlexError(f"FortiFlex config {s!r} not found; available: {names}")
        if len(matches) > 1:
            raise FlexError(f"FortiFlex config name {s!r} is ambiguous ({len(matches)} matches)")
        return config_id_of(matches[0])

    def create_config(
        self,
        program_serial: str,
        name: str,
        product_type_id: int,
        parameters: list[dict[str, Any]],
        *,
        account_id: int | None = None,
    ) -> Config:
        data = self.post(
            "configs/create",
            _drop_empty(
                {
                    "programSerialNumber": program_serial,
                    "accountId": account_id,
                    "name": name,
                    "productTypeId": product_type_id,
                    "parameters": parameters,
                }
            ),
        )
        return cast(Config, data.get("configs") or {})

    def update_config(
        self,
        config_id: int,
        *,
        name: str | None = None,
        parameters: list[dict[str, Any]] | None = None,
    ) -> Config:
        data = self.post(
            "configs/update",
            _drop_empty({"id": config_id, "name": name, "parameters": parameters}),
        )
        return cast(Config, data.get("configs") or {})

    def enable_config(self, config_id: int) -> Config:
        return cast(Config, self.post("configs/enable", {"id": config_id}).get("configs") or {})

    def disable_config(self, config_id: int) -> Config:
        return cast(Config, self.post("configs/disable", {"id": config_id}).get("configs") or {})

    # ── entitlements ───────────────────────────────────────────────────────
    def entitlements(
        self,
        *,
        config_id: int | None = None,
        program_serial: str | None = None,
        account_id: int | None = None,
        **filters: Any,
    ) -> list[Entitlement]:
        """Entitlements filtered by config, or by program (+ optional account)."""
        payload = _drop_empty(
            {
                "configId": config_id,
                "programSerialNumber": program_serial,
                "accountId": account_id,
                **filters,
            }
        )
        data = self.post("entitlements/list", payload)
        # Some programs answer with a bare list, which post() wraps as {"data": [...]}.
        return _items(data, "entitlements", Entitlement) or _items(data, "data", Entitlement)

    def create_vm_entitlements(
        self,
        config_id: int,
        *,
        count: int = 1,
        description: str = "",
        end_date: str | None = None,
        skip_pending: bool = True,
        folder_path: str = "My Assets",
    ) -> list[Entitlement]:
        """New VM entitlements. They start PENDING and turn ACTIVE in ~30 min
        unless ``skip_pending`` is honoured for the program."""
        data = self.post(
            "entitlements/vm/create",
            {
                "configId": config_id,
                "count": count,
                "description": description,
                "endDate": end_date,
                "skipPending": skip_pending,
                "folderPath": folder_path,
            },
        )
        return _items(data, "entitlements", Entitlement)

    def create_hardware_entitlements(
        self, config_id: int, serial_numbers: list[str], *, end_date: str | None = None
    ) -> list[Entitlement]:
        data = self.post(
            "entitlements/hardware/create",
            _drop_empty(
                {
                    "configId": config_id,
                    "serialNumbers": serial_numbers,
                    "endDate": end_date,
                }
            ),
        )
        return _items(data, "entitlements", Entitlement)

    def update_entitlement(
        self,
        serial: str,
        *,
        config_id: int | None = None,
        description: str | None = None,
        end_date: str | None = None,
    ) -> list[Entitlement]:
        data = self.post(
            "entitlements/update",
            _drop_empty(
                {
                    "serialNumber": serial,
                    "configId": config_id,
                    "description": description,
                    "endDate": end_date,
                }
            ),
        )
        return _items(data, "entitlements", Entitlement)

    def stop_entitlement(self, serial: str) -> list[Entitlement]:
        return _items(
            self.post("entitlements/stop", {"serialNumber": serial}),
            "entitlements",
            Entitlement,
        )

    def reactivate_entitlement(self, serial: str) -> list[Entitlement]:
        return _items(
            self.post("entitlements/reactivate", {"serialNumber": serial}),
            "entitlements",
            Entitlement,
        )

    def transfer_entitlements(
        self, serial_numbers: list[str], *, account_id: int, program_serial: str
    ) -> list[Entitlement]:
        data = self.post(
            "entitlements/transfer",
            {
                "serialNumbers": serial_numbers,
                "accountId": account_id,
                "programSerialNumber": program_serial,
            },
        )
        return _items(data, "entitlements", Entitlement)

    def regenerate_token(self, serial: str) -> str:
        """A fresh single-use VM token for ``serial`` (invalidates the old one)."""
        ents = _items(
            self.post("entitlements/vm/token", {"serialNumber": serial}),
            "entitlements",
            Entitlement,
        )
        token = ents[0].get("token") if ents else None
        if not token:
            raise FlexError(f"FortiFlex returned no token for {serial}")
        return str(token)

    def points(
        self,
        program_serial: str,
        start_date: str,
        end_date: str,
        *,
        account_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Point usage per entitlement between two dates (``YYYY-MM-DD``)."""
        data = self.post(
            "entitlements/points",
            _drop_empty(
                {
                    "programSerialNumber": program_serial,
                    "accountId": account_id,
                    "startDate": start_date,
                    "endDate": end_date,
                }
            ),
        )
        return _items(data, "entitlements", dict)

    # ── groups ─────────────────────────────────────────────────────────────
    def groups(self, account_id: int) -> list[Group]:
        """Asset folders of one account (FortiFlex 400s ``groups/list`` without
        ``accountId``; every program and config carries one)."""
        return _items(
            self.post("groups/list", {"accountId": account_id}),
            "fortiCareAssetGroups",
            Group,
        )

    def group_next_token(
        self,
        *,
        folder_path: str | None = None,
        config_id: int | None = None,
        account_id: int | None = None,
    ) -> list[Entitlement]:
        data = self.post(
            "groups/nexttoken",
            _drop_empty(
                {
                    "folderPath": folder_path,
                    "configId": config_id,
                    "accountId": account_id,
                }
            ),
        )
        return _items(data, "entitlements", Entitlement)
