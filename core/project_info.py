"""
Información centralizada del proyecto Metrics Servers API.

Capa arquitectónica: Infraestructura / Metadatos.

Responsabilidades:
    - Definir en un único lugar los metadatos de identificación del proyecto
      (nombre, versión, autor, organización, descripción, URL, licencia).
    - Ser la fuente de verdad que consumen `main.py` (FastAPI title/description),
      los logs de arranque y cualquier endpoint informativo futuro.

Qué NO debe contener este fichero:
    - Lógica de aplicación de ningún tipo.
    - Dependencias externas.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

PROJECT_NAME: str = "Metrics Servers API"

PROJECT_DESCRIPTION: str = (
    "API REST para la monitorización y gestión centralizada de servidores.\n\n"
    "Proporciona autenticación corporativa mediante LDAP/Active Directory con tokens JWT, "
    "gestión de servidores, servicios, secciones, grupos de usuarios y permisos. "
    "Integra MariaDB (datos relacionales), MongoDB (métricas y logs), "
    "MinIO (almacenamiento de objetos) y Redis (blocklist de tokens revocados)."
)

PROJECT_VERSION: str = "1.0.0"

PROJECT_AUTHOR: str = "Alejandro Gómez Blanco"

PROJECT_AUTHOR_EMAIL: str = "alejandro.g.b.a29@gmail.com"

PROJECT_COMPANY: str = "Metrics Servers Project"

PROJECT_COPYRIGHT: str = f"Copyright © 2026 {PROJECT_AUTHOR}"

PROJECT_URL: str = "https://alejandrogb.ddns.net"

PROJECT_LICENSE: str = "MIT"

PROJECT_CREATED_AT: str = "2026"

# Versión corta de la descripción, segura para cabeceras HTTP.
# Las cabeceras no pueden contener saltos de línea ni caracteres de control.
PROJECT_DESCRIPTION_SHORT: str = (
    "API REST para gestion y monitorizacion de servidores"
)
