"""
Servicio de autenticación LDAP contra Active Directory.
Equivalente a LdapAuthService.java.
"""

import logging
import re
from dataclasses import dataclass

from ldap3 import (
    ALL_ATTRIBUTES,
    SUBTREE,
    Connection,
    Server,
    SIMPLE,
    Tls,
    AUTO_BIND_TLS_BEFORE_BIND,
)
from ldap3.core.exceptions import LDAPException

from core.config import get_settings

log = logging.getLogger("api.ldap")


@dataclass
class AdUser:
    sam_account_name: str
    user_principal_name: str | None
    display_name: str | None
    mail: str | None
    member_of: list[str]


class LdapService:
    def __init__(self) -> None:
        s = get_settings()
        self._ldap_url = s.ldap_url
        self._base_dn = s.ldap_base_dn
        self._svc_dn = s.ldap_svc_dn
        self._svc_pw = s.ldap_svc_pw

    # ── Autenticación principal ────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> AdUser | None:
        if not username or not password:
            return None
        principal = self._build_upn(username)
        log.debug("LDAP authenticate principal=%s ldap_url=%s", principal, self._ldap_url)
        if not self._can_bind(principal, password):
            return None
        return self._load_user(username.strip())

    # ── Verificación de DN en AD ───────────────────────────────────────────────

    def dn_exists(self, dn: str) -> bool:
        if not dn:
            return False
        log.debug("LDAP dn_exists dn=%s", dn)
        try:
            with self._service_connection() as conn:
                conn.search(
                    search_base=dn,
                    search_filter="(objectClass=*)",
                    search_scope=SUBTREE,
                    attributes=["distinguishedName"],
                    size_limit=1,
                )
                exists = bool(conn.entries)
                log.debug("LDAP dn_exists result=%s dn=%s", exists, dn)
                return exists
        except LDAPException as exc:
            log.warning("LDAP dn_exists error dn=%s: %s", dn, exc)
            return False

    # ── Helpers internos ───────────────────────────────────────────────────────

    def _can_bind(self, principal: str, password: str) -> bool:
        try:
            server = Server(self._ldap_url, use_ssl=self._is_ldaps(), get_info=None)
            with Connection(
                server,
                user=principal,
                password=password,
                authentication=SIMPLE,
                auto_bind=True,
            ):
                log.debug("LDAP bind_ok principal=%s", principal)
                return True
        except LDAPException as exc:
            log.debug("LDAP bind_fail principal=%s: %s", principal, exc)
            return False

    def _load_user(self, username: str) -> AdUser | None:
        log.debug("LDAP load_user username=%s base_dn=%s", username, self._base_dn)
        try:
            with self._service_connection() as conn:
                search_filter = (
                    f"(&(objectCategory=person)(objectClass=user)"
                    f"(sAMAccountName={self._escape_ldap(username)}))"
                )
                conn.search(
                    search_base=self._base_dn,
                    search_filter=search_filter,
                    search_scope=SUBTREE,
                    attributes=[
                        "sAMAccountName",
                        "userPrincipalName",
                        "displayName",
                        "mail",
                        "memberOf",
                    ],
                )
                if not conn.entries:
                    log.debug("LDAP load_user not_found username=%s", username)
                    return None

                entry = conn.entries[0]
                member_of: list[str] = []
                raw = entry.memberOf.values if entry.memberOf else []
                member_of = [str(v) for v in raw]

                user = AdUser(
                    sam_account_name=str(entry.sAMAccountName),
                    user_principal_name=self._str_or_none(entry.userPrincipalName),
                    display_name=self._str_or_none(entry.displayName),
                    mail=self._str_or_none(entry.mail),
                    member_of=member_of,
                )
                log.debug("LDAP load_user ok username=%s display_name=%s groups=%d",
                          user.sam_account_name, user.display_name, len(member_of))
                return user
        except LDAPException as exc:
            raise RuntimeError(f"Error consultando Active Directory: {exc}") from exc

    def _service_connection(self) -> Connection:
        server = Server(self._ldap_url, use_ssl=self._is_ldaps(), get_info=None)
        return Connection(
            server,
            user=self._svc_dn,
            password=self._svc_pw,
            authentication=SIMPLE,
            auto_bind=True,
        )

    def _build_upn(self, username: str) -> str:
        username = username.strip()
        if "@" in username:
            return username
        return f"{username}@metrics.local"

    def _is_ldaps(self) -> bool:
        return self._ldap_url.startswith("ldaps://")

    @staticmethod
    def _escape_ldap(value: str) -> str:
        replacements = [
            ("\\", "\\5c"), ("*", "\\2a"),
            ("(", "\\28"), (")", "\\29"), ("\x00", "\\00"),
        ]
        for char, escaped in replacements:
            value = value.replace(char, escaped)
        return value

    @staticmethod
    def _str_or_none(attr) -> str | None:
        if attr is None:
            return None
        v = attr.value if hasattr(attr, "value") else attr
        return str(v) if v else None
