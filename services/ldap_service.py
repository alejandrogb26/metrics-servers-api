"""
Servicio de autenticación LDAP contra Active Directory.
Equivalente a LdapAuthService.java.

Capa arquitectónica: Infraestructura / Servicio externo (Active Directory).

Responsabilidades:
    - Autenticar usuarios validando sus credenciales mediante un bind LDAP
      (`authenticate`).
    - Cargar los atributos del usuario desde AD tras la autenticación exitosa
      (`_load_user`): nombre, mail y grupos (DNs de `memberOf`).
    - Verificar que un Distinguished Name (DN) existe en el directorio
      (`dn_exists`), usado para validar los DNs de grupo antes de persistirlos.

Qué NO debe contener este fichero:
    - Lógica de sesión, permisos ni JWT. Eso pertenece a `services/auth_service.py`
      y `core/security.py`.
    - Persistencia en base de datos. Eso pertenece a los repositorios.
    - Sincronización del registro local de usuario. Eso pertenece a
      `services/auth_service.py` (`_sync_usuario_app`).

Protocolo de autenticación (dos conexiones):
    La autenticación requiere dos conexiones LDAP separadas:
    1. **Bind de usuario** (`_can_bind`): verifica que las credenciales son
       correctas usando el propio DN/UPN del usuario. No busca atributos.
    2. **Bind de servicio** (`_load_user`): usa la cuenta de servicio configurada
       para buscar los atributos completos del usuario en el directorio.

    Este patrón es necesario porque muchos entornos AD restringen las búsquedas
    a cuentas con permisos de lectura, que los usuarios normales no siempre tienen.

Relaciones con otros módulos:
    - `core/config.py`            → `get_settings` para leer los parámetros de
                                    conexión LDAP (`ldap_url`, `ldap_base_dn`,
                                    `ldap_svc_dn`, `ldap_svc_pw`).
    - `services/auth_service.py`  → instancia `LdapService()` y llama a
                                    `authenticate` en el flujo de login.
    - `services/grupo_service.py` → instancia `LdapService()` y llama a
                                    `dn_exists` para validar DNs de grupo.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
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

# Define AdUser como una dataclass.
# Este tipo de clase se usa principalmente para almacenar datos.
# Gracias a @dataclass no es necesario escribir manualmente el constructor:
# Python lo genera a partir de los atributos declarados en la clase.
@dataclass
class AdUser:
    """
    DTO con los atributos de un usuario de Active Directory.

    Construido por `LdapService._load_user` a partir de la respuesta del
    directorio. Es el objeto de transferencia entre la capa LDAP y la capa de
    servicio de autenticación.

    Campos:
        sam_account_name:    Nombre de cuenta Windows (ej. "jsmith"). Se usa
                             como `username` en toda la aplicación y como `sub`
                             en el JWT. Es el identificador mutable del usuario.
        user_principal_name: Formato UPN (ej. "jsmith@metrics.local"). Puede
                             ser `None` si el atributo no está configurado en AD.
        display_name:        Nombre completo para mostrar (ej. "John Smith").
                             Se incluye en el JWT y en la sesión.
        mail:                Dirección de correo electrónico. Opcional en AD.
        member_of:           Lista de DNs completos de los grupos AD a los que
                             pertenece el usuario (ej.
                             ["CN=DevOps,OU=Groups,DC=metrics,DC=local"]).
                             `AuthService` usa esta lista para resolver el grupo
                             autorizado del sistema mediante
                             `GrupoRepository.find_by_any_dn`.
    """

    sam_account_name: str
    user_principal_name: str | None
    display_name: str | None
    mail: str | None
    member_of: list[str]


class LdapService:
    """
    Cliente LDAP/AD para autenticación y consultas de directorio.

    Lee los parámetros de conexión de `get_settings()` en el constructor.
    Crea conexiones bajo demanda (no mantiene conexiones persistentes) para
    evitar problemas de timeout y reconexión. Cada operación abre y cierra
    su propia conexión mediante context managers (`with Connection(...)`).

    Parámetros de conexión almacenados:
        _ldap_url:  URL del servidor LDAP (ej. "ldaps://ad.metrics.local:636").
        _base_dn:   DN base para las búsquedas de usuarios.
        _svc_dn:    DN de la cuenta de servicio para búsquedas en directorio.
        _svc_pw:    Contraseña de la cuenta de servicio.
    """

    def __init__(self) -> None:
        s = get_settings()
        self._ldap_url = s.ldap_url
        self._base_dn = s.ldap_base_dn
        self._svc_dn = s.ldap_svc_dn
        self._svc_pw = s.ldap_svc_pw

    # ── Autenticación principal ────────────────────────────────────────────────

    def authenticate(self, username: str, password: str) -> AdUser | None:
        """
        Autentica un usuario contra AD y devuelve sus atributos.

        Implementa el protocolo de dos conexiones documentado en el módulo:
        primero intenta un bind con las credenciales del usuario para verificar
        que son correctas; si tiene éxito, usa la cuenta de servicio para cargar
        los atributos completos del usuario.

        El `username` se normaliza a UPN mediante `_build_upn` antes del bind:
        si ya contiene `@` se usa tal cual; si no, se añade `@metrics.local`.
        La búsqueda posterior en `_load_user` usa el `username` sin el dominio
        (`sAMAccountName`).

        Args:
            username: Nombre de cuenta del usuario (ej. "jsmith" o
                      "jsmith@metrics.local"). Se recortan espacios.
            password: Contraseña del usuario en texto plano. No se almacena
                      ni se registra en ningún log.

        Retorna:
            `AdUser` con los atributos del usuario si la autenticación tiene
            éxito, `None` si las credenciales son incorrectas o los campos
            están vacíos.

        Lanza:
            `RuntimeError` si `_load_user` falla por error de conectividad con
            AD (propagado desde `_load_user`).
        """
        if not username or not password:
            return None
        principal = self._build_upn(username)
        log.debug("LDAP authenticate principal=%s ldap_url=%s", principal, self._ldap_url)
        if not self._can_bind(principal, password):
            return None
        return self._load_user(username.strip())

    # ── Verificación de DN en AD ───────────────────────────────────────────────

    def dn_exists(self, dn: str) -> bool:
        """
        Verifica que un Distinguished Name existe en el directorio AD.

        Usa la cuenta de servicio para buscar el objeto con el DN proporcionado
        como `search_base`, con filtro `(objectClass=*)` (cualquier tipo de
        objeto) y `size_limit=1` (solo necesita confirmar existencia).

        Política de fallo:
            Si se produce un `LDAPException` (AD no accesible, DN mal formado,
            permisos insuficientes de la cuenta de servicio), devuelve `False`
            y registra un `WARNING` en el log. Esto puede causar que un DN
            válido sea rechazado si AD está temporalmente caído. Se trata de
            un fallo-seguro: preferible rechazar un DN que aceptar uno inválido.

        Args:
            dn: Distinguished Name a verificar (ej.
                "CN=DevOps,OU=Groups,DC=metrics,DC=local"). Si está vacío,
                devuelve `False` directamente sin consultar AD.

        Retorna:
            `True` si el objeto con ese DN existe en AD; `False` si no existe,
            está vacío, o se produjo un error de conectividad.
        """
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
        """
        Intenta un bind LDAP con las credenciales del usuario.

        Crea una conexión con `auto_bind=True`, que lanza `LDAPException`
        automáticamente si el bind falla (credenciales incorrectas, cuenta
        bloqueada, etc.). El context manager cierra la conexión al salir.

        `get_info=None` desactiva la descarga del schema del servidor en la
        negociación inicial, reduciendo la latencia del bind.

        Args:
            principal: UPN del usuario (ej. "jsmith@metrics.local").
            password:  Contraseña del usuario. No se registra en ningún log.

        Retorna:
            `True` si el bind tiene éxito; `False` ante cualquier `LDAPException`.
        """
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
        """
        Carga los atributos de un usuario desde AD usando la cuenta de servicio.

        Busca el usuario por `sAMAccountName` con el filtro estándar de AD:
        `(&(objectCategory=person)(objectClass=user)(sAMAccountName=...))`.
        El `username` se escapa con `_escape_ldap` para prevenir inyección LDAP.

        Atributos recuperados: `sAMAccountName`, `userPrincipalName`,
        `displayName`, `mail`, `memberOf`.

        `memberOf` contiene los DNs de los grupos AD del usuario. En ldap3,
        `entry.memberOf.values` devuelve una colección de valores; se convierte
        a `list[str]` para su uso posterior en `GrupoRepository.find_by_any_dn`.

        A diferencia de `_can_bind` (que devuelve `False` ante errores LDAP),
        este método eleva `RuntimeError` envolviendo la `LDAPException`. Esto
        es intencional: si el bind del usuario tuvo éxito pero la búsqueda
        falla, indica un problema de infraestructura (cuenta de servicio sin
        permisos, AD caído) que merece propagarse como error 500.

        Args:
            username: `sAMAccountName` del usuario, ya recortado de espacios.

        Retorna:
            `AdUser` construido con los atributos del directorio, o `None` si
            el usuario no se encuentra en el árbol de búsqueda.

        Lanza:
            `RuntimeError` si se produce un error LDAP durante la búsqueda.
        """
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
        """
        Crea una conexión LDAP autenticada con la cuenta de servicio.

        Devuelve un objeto `Connection` de ldap3 con `auto_bind=True`, que
        puede usarse como context manager (`with self._service_connection() as
        conn`). El bind se realiza en la construcción; la desconexión ocurre
        al salir del bloque `with`.

        La cuenta de servicio (`_svc_dn` / `_svc_pw`) tiene permisos de lectura
        sobre el directorio y se usa para todas las búsquedas que no requieren
        las credenciales del usuario final.
        """
        server = Server(self._ldap_url, use_ssl=self._is_ldaps(), get_info=None)
        return Connection(
            server,
            user=self._svc_dn,
            password=self._svc_pw,
            authentication=SIMPLE,
            auto_bind=True,
        )

    def _build_upn(self, username: str) -> str:
        """
        Construye el User Principal Name para el bind LDAP.

        Si el `username` ya contiene `@` (formato UPN completo), se devuelve
        tal cual tras recortar espacios. Si no, se añade el sufijo de dominio
        `@metrics.local`.

        El dominio `metrics.local` está definido de forma fija en el código.
        Si el entorno usa un dominio diferente (ej. `empresa.com`), este método
        debe actualizarse o el sufijo debe hacerse configurable.

        Args:
            username: Nombre de cuenta del usuario, con o sin dominio.

        Retorna:
            UPN en formato "usuario@dominio" listo para el bind LDAP.
        """
        username = username.strip()
        if "@" in username:
            return username
        return f"{username}@metrics.local"

    def _is_ldaps(self) -> bool:
        """
        Determina si la URL de LDAP usa SSL/TLS (protocolo `ldaps://`).

        Retorna:
            `True` si `_ldap_url` comienza por `ldaps://`; `False` en caso
            contrario (protocolo `ldap://` sin cifrado).
        """
        return self._ldap_url.startswith("ldaps://")

    @staticmethod
    def _escape_ldap(value: str) -> str:
        """
        Escapa caracteres especiales en valores de filtros LDAP (RFC 4515).

        Previene inyección LDAP al insertar el nombre de usuario directamente
        en el filtro de búsqueda `(sAMAccountName=<valor>)`. Sin este escape,
        un username como `*)(uid=*))(|(uid=*` podría alterar el filtro.

        Caracteres escapados:
            `\\` → `\\5c`,  `*` → `\\2a`,  `(` → `\\28`,
            `)` → `\\29`,   null byte → `\\00`

        Args:
            value: Cadena a escapar (normalmente el `sAMAccountName` del usuario).

        Retorna:
            Cadena con los caracteres especiales reemplazados por sus
            representaciones de escape LDAP.
        """
        replacements = [
            ("\\", "\\5c"), ("*", "\\2a"),
            ("(", "\\28"), (")", "\\29"), ("\x00", "\\00"),
        ]
        for char, escaped in replacements:
            value = value.replace(char, escaped)
        return value

    @staticmethod
    def _str_or_none(attr) -> str | None:
        """
        Convierte un atributo ldap3 a `str` o `None`.

        Los atributos de ldap3 son objetos con propiedad `.value`, no cadenas
        simples. Si el atributo tiene `.value`, se extrae; si no, se usa
        directamente. Devuelve `None` si el atributo es `None`, vacío, o su
        valor es falsy.

        Args:
            attr: Atributo ldap3 (objeto con `.value`) o valor directo.

        Retorna:
            `str` con el valor del atributo, o `None` si está vacío/ausente.
        """
        if attr is None:
            return None
        v = attr.value if hasattr(attr, "value") else attr
        return str(v) if v else None
