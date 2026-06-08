# Metrics Servers API

API REST de monitorización de servidores. Reimplementación en Python/FastAPI de la API
original escrita en Java (Jakarta EE / TomEE), manteniendo exactamente los mismos contratos
HTTP para garantizar compatibilidad con los clientes existentes (Flutter móvil/web y Java Swing).

---

## Tabla de contenidos

1. [Stack tecnológico](#1-stack-tecnológico)
2. [Arquitectura en capas](#2-arquitectura-en-capas)
3. [Estructura del proyecto](#3-estructura-del-proyecto)
4. [Flujo de una petición](#4-flujo-de-una-petición)
5. [Seguridad y autorización](#5-seguridad-y-autorización)
6. [Modelo de datos](#6-modelo-de-datos)
7. [Almacenamiento de objetos (MinIO)](#7-almacenamiento-de-objetos-minio)
8. [SSH Probe](#8-ssh-probe)
9. [Variables de entorno](#9-variables-de-entorno)
10. [Instalación y arranque](#10-instalación-y-arranque)
11. [Referencia de endpoints](#11-referencia-de-endpoints)
12. [Decisiones de diseño](#12-decisiones-de-diseño)
13. [Notas para desarrolladores](#13-notas-para-desarrolladores)

---

## 1. Stack tecnológico

| Componente | Java (original) | Python (este proyecto) |
|---|---|---|
| Framework web | JAX-RS / Apache CXF | **FastAPI** 0.115+ |
| ORM / BD relacional | JDBC manual | **SQLModel** (SQLAlchemy) |
| BD relacional | MariaDB | MariaDB (mismo esquema) |
| BD de métricas | MongoDB | MongoDB (**pymongo** 4.x) |
| Almacenamiento objetos | MinIO SDK Java | **MinIO** (minio-py 7.x) |
| Autenticación | LDAP / AD (javax.naming) | **ldap3** 2.x |
| JWT | jjwt | **python-jose** (HMAC-SHA256 / HS256) |
| Blocklist JWT | — | **Redis** 5.x (TTL por token) |
| SSH probe | SSHJ | **paramiko** 3.x |
| Configuración | dotenv-java | **pydantic-settings** 2.x |
| Servidor ASGI | TomEE | **Uvicorn** (standard) |
| Python mínimo | — | **3.14+** |

---

## 2. Arquitectura en capas

El proyecto sigue una arquitectura en capas estricta. Cada capa solo depende de
la inmediatamente inferior; ninguna capa salta hacia arriba.

```
┌──────────────────────────────────────────────────────────────┐
│                        CLIENTES                              │
│          Flutter (móvil / web)   ·   Java Swing              │
└──────────────────────────┬───────────────────────────────────┘
                           │ HTTP / REST (JSON, camelCase)
┌──────────────────────────▼───────────────────────────────────┐
│  CAPA HTTP  —  routers/                                      │
│  FastAPI APIRouter · Pydantic validation · HTTPBearer auth   │
│  Convierte HTTP → DTOs · responde HTTP desde DTOs            │
└──────────────────────────┬───────────────────────────────────┘
                           │ DTOs (Pydantic)
┌──────────────────────────▼───────────────────────────────────┐
│  CAPA APLICACIÓN  —  services/                               │
│  Orquesta la lógica de negocio · coordina colaboradores      │
│  No conoce HTTP · trabaja con objetos de dominio             │
└────────┬──────────────────┬──────────────────────────────────┘
         │                  │
         ▼                  ▼
┌────────────────┐  ┌───────────────────────────────────────────┐
│  REPOSITORIOS  │  │  SERVICIOS DE INFRAESTRUCTURA             │
│  repositories/ │  │  services/ (ldap, minio, ssh_probe)       │
│  MariaDB +     │  │  core/ (security, token_blocklist)        │
│  MongoDB       │  │  Sistemas externos: LDAP, MinIO, Redis    │
└────────┬───────┘  └───────────────────────────────────────────┘
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│  CAPA DE PERSISTENCIA                                        │
│  MariaDB (SQLModel/SQLAlchemy)   ·   MongoDB (pymongo)       │
└──────────────────────────────────────────────────────────────┘
```

### Paquete `core/`

Infraestructura transversal disponible para todas las capas:

| Módulo | Responsabilidad |
|---|---|
| `config.py` | `Settings` (pydantic-settings) + `get_settings()` singleton cacheado |
| `database.py` | Engine SQLAlchemy + dependencia `get_session` para inyección en routers |
| `mongo.py` | Cliente MongoDB singleton (`@lru_cache`) |
| `minio_client.py` | Cliente MinIO singleton (`@lru_cache`) |
| `security.py` | Emisión y validación de JWT (HMAC-SHA256) |
| `token_blocklist.py` | Blocklist de JTI en Redis con TTL automático |
| `dependencies.py` | Dependencias FastAPI: `get_current_user`, `require_permission` |
| `logging_config.py` | Configuración centralizada del sistema de logging |
| `debug_middleware.py` | Middleware que loga peticiones HTTP con duración (solo debug) |
| `project_info.py` | Constantes del proyecto: nombre, versión, autor, URL, licencia — fuente de verdad para Swagger, logs y cabeceras |
| `app_metadata_middleware.py` | `AppMetadataMiddleware`: añade cabeceras `X-App-*` a todas las respuestas |

### Paquete `exceptions/`

| Módulo | Responsabilidad |
|---|---|
| `errors.py` | Jerarquía de excepciones del dominio (`DaoException`, `ProbeException`, …) |
| `handlers.py` | Manejadores globales FastAPI que traducen excepciones a JSON estructurado |

---

## 3. Estructura del proyecto

```
api-py/
├── main.py                         # Composición: app, middleware, routers, lifespan
├── pyproject.toml                  # Dependencias y metadatos del paquete
├── .env.example                    # Plantilla de variables de entorno
│
├── core/
│   ├── config.py                   # Settings + get_settings()
│   ├── database.py                 # Engine SQLModel + get_session
│   ├── dependencies.py             # get_current_user · require_permission(name)
│   ├── security.py                 # create_token · decode_token
│   ├── token_blocklist.py          # add_to_blocklist · is_blocked (Redis)
│   ├── mongo.py                    # get_mongo_client · get_mongo_db
│   ├── minio_client.py             # get_minio_client
│   ├── logging_config.py           # setup_logging(debug)
│   ├── debug_middleware.py         # DebugLoggingMiddleware
│   ├── project_info.py             # PROJECT_NAME · VERSION · AUTHOR · URL · LICENSE (fuente de verdad)
│   └── app_metadata_middleware.py  # AppMetadataMiddleware → cabeceras X-App-* en todas las respuestas
│
├── models/
│   ├── common.py                   # PagedResponse[T] · BulkResult · LoginRequest/Response
│   ├── ambito.py                   # Ambito (tabla) · AmbitoRead
│   ├── permiso.py                  # Permiso (tabla) · PermisoRead (con AmbitoRead embebido)
│   ├── seccion.py                  # Seccion (tabla) · SeccionCreate · SeccionRead
│   ├── servicio.py                 # Servicio (tabla) · ServicioCreate · ServicioRead
│   ├── servidor.py                 # Servidor (tabla) · ServidorCreate · ServidorRead
│   │                               #   · ServidorPatch (interno) · ServidorPatchRequest (público)
│   ├── grupo.py                    # Grupo (tabla) · GrupoCreate · GrupoRead
│   │                               #   · GrupoPermisoGlobal · GrupoSeccion
│   ├── usuario.py                  # UsuarioApp (tabla)
│   └── permission_map.py           # PermissionMap[T]: global_perms + sections dict
│
├── repositories/
│   ├── ambito_repo.py              # Catálogo de ámbitos (solo lectura)
│   ├── permiso_repo.py             # Catálogo de permisos + JOIN con ambito
│   ├── seccion_repo.py             # CRUD de secciones
│   ├── servicio_repo.py            # CRUD de servicios + update_logo
│   ├── servidor_repo.py            # CRUD + bulk + servicios + foto; anti-N+1 batch
│   ├── grupo_repo.py               # CRUD grupos + gestión de permisos globales/sección
│   ├── usuario_repo.py             # find_by_username · update_foto
│   ├── auth_repo.py                # build_session (carga del PermissionMap en login)
│   └── mongo_repo.py               # get_metrics · update_server_id · delete_by_server_id
│
├── services/
│   ├── ldap_service.py             # authenticate · get_user_groups (two-bind pattern)
│   ├── auth_service.py             # login() · _sync_usuario_app()
│   ├── minio_service.py            # upload · get_presigned_url · delete
│   ├── ssh_probe_service.py        # ask_server() → ServidorInfo
│   ├── servidor_service.py         # CRUD + bulk + foto + métricas + SSH probe
│   ├── servicio_service.py         # CRUD + gestión de logo
│   ├── seccion_service.py          # CRUD + paginación
│   ├── grupo_service.py            # GrupoService (CRUD) + GrupoPermisosService
│   ├── usuario_service.py          # Foto de perfil
│   ├── ambito_service.py           # Lectura paginada de ámbitos
│   └── permiso_service.py          # Lectura paginada de permisos
│
├── routers/
│   ├── info.py                     # GET /info  (público — metadatos del proyecto)
│   ├── health.py                   # GET /health/status  (público)
│   ├── auth.py                     # POST /auth/login · POST /auth/logout
│   ├── servidor.py                 # /servidor  (10 endpoints)
│   ├── servicio.py                 # /servicio  (6 endpoints)
│   ├── seccion.py                  # /seccion   (5 endpoints)
│   ├── grupo.py                    # /grupos    (6 endpoints)
│   ├── grupo_permisos.py           # /grupos/{id}/permisos  (4 endpoints)
│   ├── permiso.py                  # /permisos  (2 endpoints, solo lectura)
│   ├── ambito.py                   # /ambitos   (2 endpoints, solo lectura)
│   └── usuario.py                  # POST /usuario/foto
│
└── exceptions/
    ├── errors.py                   # DaoException · ProbeException · LdapException · …
    └── handlers.py                 # Manejadores globales → {"error": …, "message": …}
```

---

## 4. Flujo de una petición

### Petición autenticada típica (ejemplo: `GET /servidor`)

```
Cliente
  │
  │  GET /servidor?page=0&size=20
  │  Authorization: Bearer <jwt>
  ▼
Router (routers/servidor.py)
  │  1. FastAPI extrae HTTPBearer → HTTPAuthorizationCredentials
  │  2. require_permission("AUDIT_SERV") valida el JWT y comprueba el permiso
  │  3. visible_section_ids(user, "AUDIT_SERV") → set[int] | None
  │  4. Handler llama a ServidorService(session).find_all(page, size, section_ids)
  ▼
Service (services/servidor_service.py)
  │  5. Calcula offset = page * size
  │  6. Delega en ServidorRepository.find_all(offset, limit, section_ids)
  │  7. Para cada servidor: _resolve_imagen_url() → URL presignada MinIO (local)
  │  8. Devuelve (list[ServidorRead], total)
  ▼
Repository (repositories/servidor_repo.py)
  │  9. SELECT con WHERE seccion_id IN (...) si section_ids no es None
  │  10. COUNT(*) para el total (query separada)
  │  11. Batch-load de servicios asociados (anti-N+1)
  │  12. Construye ServidorRead para cada fila
  ▼
Router
  │  13. Envuelve en PagedResponse[ServidorRead]
  │  14. FastAPI serializa a JSON (snake_case → camelCase por alias_generator)
  ▼
Cliente
     HTTP 200  {"content": [...], "totalElements": N, ...}
```

### Flujo de login

```
POST /auth/login  {username, password}
  │
  ├─ LdapService.authenticate(username, password)
  │    Bind con credenciales del usuario → éxito o LdapException
  │
  ├─ LdapService.get_user_groups(username)
  │    Bind con cuenta de servicio → busca memberOf del usuario
  │
  ├─ AuthService._sync_usuario_app(username, ad_object_id)
  │    Crea o actualiza el UsuarioApp en MariaDB (foto, grupos AD)
  │
  ├─ AuthRepository.build_session(username)
  │    Carga PermissionMap: permisos globales + permisos por sección
  │
  ├─ security.create_token(claims)
  │    JWT firmado con HMAC-SHA256, exp = ahora + JWT_EXPIRATION_SECONDS
  │
  └─ HTTP 200  {token, username, superadmin, permissions, fotoUrl}
```

---

## 5. Seguridad y autorización

### Autenticación JWT

Todos los endpoints protegidos requieren la cabecera:

```
Authorization: Bearer <token>
```

El token es un JWT firmado con HMAC-SHA256 que contiene:

| Claim | Descripción |
|---|---|
| `sub` | Nombre de usuario (`sam_account_name` en AD) |
| `jti` | Identificador único del token (UUID) |
| `iat` | Timestamp de emisión |
| `exp` | Timestamp de expiración (`iat + JWT_EXPIRATION_SECONDS`) |

### Logout y blocklist

El logout **no es stateless**. Al hacer `POST /auth/logout`, el `jti` del token
se almacena en Redis con un TTL igual al tiempo de expiración residual. En cada
petición posterior, `require_permission` comprueba la blocklist antes de procesar
la request. Esto garantiza que los tokens revocados queden invalidados incluso
antes de su expiración natural.

### Modelo de permisos

Los permisos siguen el formato `{operacion}_{ambito}`:

| Permiso | Descripción |
|---|---|
| `AUDIT_SERV` | Lectura de servidores, secciones y servicios |
| `MODIFY_SERV` | Escritura sobre servidores, secciones y servicios |
| `AUDIT_USER` | Lectura de usuarios, grupos y permisos |
| `MODIFY_USER` | Escritura sobre usuarios, grupos y permisos |
| `AUDIT_SYS` | Lectura de ámbitos y configuración del sistema |

Los permisos se asignan a **grupos**, no a usuarios individuales. Un usuario
hereda los permisos del grupo AD al que pertenece.

### Niveles de asignación

Un permiso puede asignarse a un grupo de dos formas:

- **Global**: el grupo tiene ese permiso sobre todos los recursos del sistema.
- **Por sección**: el grupo tiene ese permiso solo sobre los servidores de esa sección.

Los **superadmins** (`grupo.superadmin = true`) tienen acceso total sin restricción
de sección y sin necesidad de permisos explícitos.

### Visibilidad por sección

Todos los endpoints de servidor aplican un filtro de visibilidad:

```python
# En los routers:
section_ids = visible_section_ids(user, "AUDIT_SERV")
# None    → superadmin, sin restricción
# set[int]→ solo servidores de esas secciones

# Semántica de seguridad por oscuridad:
# Un servidor no accesible devuelve 404, no 403,
# para no revelar la existencia del recurso.
```

### Dependencias de autenticación

```python
# Solo verifica que el JWT es válido (sin comprobar permisos).
# Usado en endpoints propios del usuario autenticado (ej: subir foto de perfil).
user: UsuarioApp = Depends(get_current_user)

# Verifica JWT + comprueba que el usuario tiene el permiso indicado.
_user = Depends(require_permission("AUDIT_SERV"))

# Sin nombre de permiso: solo valida el JWT. El check de autorización
# se hace manualmente en el handler (ej: set_superadmin requiere superadmin).
_user = Depends(require_permission())
```

---

## 6. Modelo de datos

### Patrón de modelos Pydantic / SQLModel

Cada entidad sigue el patrón de tres niveles:

```
XxxBase          # Campos comunes sin id. No se usa directamente.
├── Xxx          # ORM table=True. Se persiste en BD.
├── XxxCreate    # DTO de entrada para creación (alias camelCase).
├── XxxRead      # DTO de salida (alias camelCase, campos calculados incluidos).
└── XxxPatch     # DTO de actualización parcial (todos los campos opcionales).
```

Todos los DTOs de entrada/salida usan `alias_generator = to_camel` con
`populate_by_name = True`, por lo que la API acepta y emite JSON en camelCase
aunque internamente use snake_case.

### Entidades principales

```
secciones
└── servidores (1:N)
    └── servidores_servicios → servicios (N:M)

grupos
├── grupo_permiso_global  (grupo_id, permiso_id)
└── grupo_seccion         (grupo_id, seccion_id, permiso_id)

permisos → ambitos (N:1)

usuarios_app → grupos AD (vínculo en memoria en el JWT, no FK en BD)
```

### Paginación

Todos los listados devuelven un `PagedResponse[T]`:

```json
{
  "content": [...],
  "totalElements": 42,
  "totalPages": 3,
  "page": 0,
  "size": 20
}
```

Los parámetros de query son `page` (base 0) y `size`. La implementación
usa dos queries: `COUNT(*)` + `SELECT LIMIT/OFFSET`. Existe una condición de
carrera teórica entre ambas que se acepta como trade-off de simplicidad.

### Operaciones en lote

Los endpoints bulk devuelven un `BulkResult`:

```json
{
  "total": 5,
  "ok": 4,
  "failed": 1,
  "errors": ["server_x: no se pudieron obtener datos SSH obligatorios"]
}
```

Cuando todos los elementos se procesan correctamente el código de respuesta
es **201**. Si alguno falla pero al menos uno tiene éxito, es **207 Multi-Status**.

---

## 7. Almacenamiento de objetos (MinIO)

MinIO actúa como almacén S3-compatible para ficheros binarios. Se usan tres buckets:

| Bucket (variable) | Contenido | Patrón de nombre de objeto |
|---|---|---|
| `BUCKET_USERS` | Fotos de perfil de usuarios | `user_{username}_{timestamp_ms}{ext}` |
| `BUCKET_SERVIDORES` | Imágenes de servidores | `server_{id}_{timestamp_ms}{ext}` |
| `BUCKET_SERVICIOS` | Logos de servicios | `servicio_{id}_{timestamp_ms}{ext}` |

### Política de errores por operación

| Operación | En caso de fallo |
|---|---|
| `upload` | Propaga la excepción al llamante (HTTP 500) |
| `get_presigned_url` | Devuelve `None`; el cliente muestra un placeholder |
| `delete` | Silencia el error (best-effort; el objeto puede ya no existir) |

Las URLs presignadas tienen una expiración de **1 hora**. La generación de la URL
es un cálculo HMAC local (sin llamada de red a MinIO), por lo que es seguro
llamarla una vez por elemento en listados paginados.

---

## 8. SSH Probe

Al registrar un servidor (individual o en lote), la API ejecuta un probe SSH para
obtener datos de diagnóstico del host remoto:

| Comando | Campo almacenado |
|---|---|
| `hostname` | `hostname` |
| `cat /etc/os-release \| grep PRETTY_NAME …` | `pretty_os` |
| `uname -m` | `arch` |
| `uname -r` | `kernel` |

### Comportamiento por operación

| Operación | Fallo del probe |
|---|---|
| `POST /servidor` (insert individual) | **Fatal** → HTTP 502 (`ProbeException`) |
| `POST /servidor/bulk` (insert en lote) | El elemento falla, el bulk continúa |
| `PATCH /servidor/{id}` (update con cambio de DNS) | **Silenciado** sin log |

### Paralelismo en bulk

En `insert_bulk`, los probes SSH se ejecutan en paralelo con un
`ThreadPoolExecutor` de hasta **10 hilos** (`_MAX_PROBE_WORKERS`). Esto evita
el tiempo N × TIMEOUT en el peor caso cuando hay servidores inalcanzables.
Los inserts en BD se realizan secuencialmente (el driver SQL no es thread-safe).

---

## 9. Variables de entorno

Copia `.env.example` como `.env` y rellena los valores. Las variables sin valor
por defecto son **obligatorias**; la aplicación no arrancará si falta alguna.

### MariaDB

| Variable | Por defecto | Descripción |
|---|---|---|
| `DB_HOST` | — | Hostname del servidor MariaDB |
| `DB_PORT` | `3306` | Puerto TCP |
| `DB_NAME` | — | Nombre de la base de datos |
| `DB_USER` | — | Usuario de conexión |
| `DB_PASSWORD` | — | Contraseña de conexión |

### MongoDB

| Variable | Por defecto | Descripción |
|---|---|---|
| `MONGO_URI` | — | URI de conexión (`mongodb://host:port`) |
| `MONGO_DB` | — | Nombre de la base de datos de métricas |

### LDAP / Active Directory

| Variable | Por defecto | Descripción |
|---|---|---|
| `LDAP_URL` | — | URL del servidor LDAP (`ldap://host`) |
| `LDAP_BASE_DN` | — | Base DN de búsqueda de usuarios |
| `LDAP_SVC_DN` | — | DN de la cuenta de servicio con permisos de lectura |
| `LDAP_SVC_PW` | — | Contraseña de la cuenta de servicio |

### MinIO

| Variable | Por defecto | Descripción |
|---|---|---|
| `MINIO_ENDPOINT` | — | Endpoint S3 (`http://host:9000`) |
| `MINIO_ACCESS_KEY` | — | Access key |
| `MINIO_SECRET_KEY` | — | Secret key |
| `BUCKET_USERS` | `usuarios` | Bucket para fotos de perfil |
| `BUCKET_SERVIDORES` | `servidores` | Bucket para imágenes de servidores |
| `BUCKET_SERVICIOS` | `servicios` | Bucket para logos de servicios |

### JWT

| Variable | Por defecto | Descripción |
|---|---|---|
| `JWT_SECRET` | — | Clave HMAC para firma de tokens. Generar con `openssl rand -hex 32`. Los valores triviales (`secret`, `changeme`, etc.) hacen fallar el arranque. |
| `JWT_EXPIRATION_SECONDS` | `28800` | Validez del token en segundos (8 h por defecto) |

### Redis

| Variable | Por defecto | Descripción |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | URI del servidor Redis para la blocklist de JWT |

### CORS

| Variable | Por defecto | Descripción |
|---|---|---|
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:8080"]` | Lista JSON de orígenes permitidos. Usar `["*"]` desactiva `allow_credentials` automáticamente (requerido por la spec CORS). Solo afecta al cliente Flutter web; los clientes móvil y Swing no envían cabeceras CORS. |

### SSH Probe

| Variable | Por defecto | Descripción |
|---|---|---|
| `SSH_PROBE_USER` | — | Usuario SSH compartido para todos los servidores monitorizados |
| `SSH_PROBE_PASSWORD` | — | Contraseña SSH de la cuenta de probe |

### Debug

| Variable | Por defecto | Descripción |
|---|---|---|
| `APP_DEBUG` | `false` | `true` activa logging HTTP detallado, stacktraces en respuestas de error y nivel DEBUG en todas las capas. **No usar en producción.** |

---

## 10. Instalación y arranque

### Requisitos previos

- Python **3.14+**
- MariaDB con el esquema existente
- MongoDB corriendo
- MinIO corriendo
- Redis corriendo
- Servidor LDAP/AD accesible
- Los servidores a registrar deben ser accesibles por SSH con las credenciales configuradas

### Instalar dependencias

```bash
# Con pip (entorno virtual recomendado)
python -m venv .venv
source .venv/bin/activate
pip install -e .

# Con uv (recomendado para desarrollo)
uv sync
```

### Dependencias de desarrollo (tests)

```bash
# Con pip
pip install -e ".[dev]"

# Con uv
uv sync --extra dev
```

### Configurar el entorno

```bash
cp .env.example .env
# Editar .env con los valores reales del entorno
```

### Arrancar el servidor

```bash
# Modo desarrollo (recarga automática al modificar ficheros)
uvicorn main:app --reload --host 0.0.0.0 --port 8080

# O directamente con Python (equivalente, activa reload automáticamente)
python main.py

# Modo producción (sin reload, workers múltiples)
uvicorn main:app --host 0.0.0.0 --port 8080 --workers 4
```

### Documentación interactiva

Con el servidor corriendo:

- **Swagger UI**: `http://localhost:8080/docs`
- **ReDoc**: `http://localhost:8080/redoc`
- **OpenAPI JSON**: `http://localhost:8080/openapi.json`
- **Info del proyecto**: `http://localhost:8080/info`

### Cabeceras HTTP de identificación

Todas las respuestas incluyen estas cabeceras, sin importar el endpoint ni el código de estado:

```http
X-App-Name: Metrics Servers API
X-App-Version: 1.0.0
X-App-Author: Alejandro Gómez Blanco
X-App-Description: API REST para gestion y monitorizacion de servidores
```

Los clientes browser pueden leerlas desde JavaScript porque están listadas en
`Access-Control-Expose-Headers` (configurado en el `CORSMiddleware` de `main.py`).

---

## 11. Referencia de endpoints

### Información y salud

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| GET | `/info` | — | Metadatos del proyecto: nombre, versión, autor, licencia, URL |
| GET | `/health/status` | — | Estado de la API (siempre HTTP 200) |

### Autenticación

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/auth/login` | — | Login LDAP → JWT |
| POST | `/auth/logout` | JWT | Invalida el token en la blocklist Redis |

### Servidores

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/servidor` | `AUDIT_SERV` | Listar servidores (paginado, filtrado por sección) |
| GET | `/servidor/{id}` | `AUDIT_SERV` | Obtener servidor por ID |
| POST | `/servidor` | `MODIFY_SERV` | Crear servidor (probe SSH obligatorio) |
| POST | `/servidor/bulk` | `MODIFY_SERV` | Crear servidores en lote (probes en paralelo) |
| PATCH | `/servidor/{id}` | `MODIFY_SERV` | Actualizar servidor |
| DELETE | `/servidor/{id}` | `MODIFY_SERV` | Eliminar servidor |
| DELETE | `/servidor/bulk` | `MODIFY_SERV` | Eliminar servidores en lote |
| POST | `/servidor/{id}/servicios` | `MODIFY_SERV` | Asociar servicios al servidor |
| DELETE | `/servidor/{id}/servicios` | `MODIFY_SERV` | Desasociar servicios del servidor |
| POST | `/servidor/{id}/foto` | `MODIFY_SERV` | Subir imagen del servidor |
| GET | `/servidor/{serverId}/metrics` | `AUDIT_SERV` | Métricas MongoDB (ventana en minutos) |

### Servicios

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/servicio` | `AUDIT_SERV` | Listar servicios (paginado) |
| GET | `/servicio/{id}` | `AUDIT_SERV` | Obtener servicio por ID |
| POST | `/servicio` | `MODIFY_SERV` | Crear servicio |
| PATCH | `/servicio/{id}` | `MODIFY_SERV` | Actualizar servicio |
| DELETE | `/servicio/{id}` | `MODIFY_SERV` | Eliminar servicio |
| POST | `/servicio/{id}/logo` | `MODIFY_SERV` | Subir logo del servicio |

### Secciones

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/seccion` | `AUDIT_SERV` | Listar secciones (paginado) |
| GET | `/seccion/{id}` | `AUDIT_SERV` | Obtener sección por ID |
| POST | `/seccion` | `MODIFY_SERV` | Crear sección |
| PATCH | `/seccion/{id}` | `MODIFY_SERV` | Actualizar sección |
| DELETE | `/seccion/{id}` | `MODIFY_SERV` | Eliminar sección |

### Grupos

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/grupos` | `AUDIT_USER` | Listar grupos (paginado) |
| GET | `/grupos/{id}` | `AUDIT_USER` | Obtener grupo por ID |
| POST | `/grupos/bulk` | `MODIFY_USER` | Crear grupos en lote |
| PATCH | `/grupos/{id}` | `MODIFY_USER` | Actualizar grupo |
| PATCH | `/grupos/{id}/superadmin` | JWT (superadmin manual) | Cambiar flag superadmin |
| DELETE | `/grupos/bulk` | `MODIFY_USER` | Eliminar grupos en lote |

### Permisos de grupo

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| PUT | `/grupos/{id}/permisos` | `MODIFY_USER` | Reemplazar todos los permisos del grupo |
| PATCH | `/grupos/{id}/permisos/global` | `MODIFY_USER` | Añadir/quitar permisos globales |
| PUT | `/grupos/{id}/permisos/secciones/{secId}` | `MODIFY_USER` | Reemplazar permisos de una sección |
| PATCH | `/grupos/{id}/permisos/secciones/{secId}` | `MODIFY_USER` | Añadir/quitar permisos de una sección |

### Permisos y ámbitos (catálogo, solo lectura)

| Método | Ruta | Permiso | Descripción |
|---|---|---|---|
| GET | `/permisos` | `AUDIT_USER` | Listar permisos (con ámbito embebido) |
| GET | `/permisos/{id}` | `AUDIT_USER` | Obtener permiso por ID |
| GET | `/ambitos` | `AUDIT_SYS` | Listar ámbitos |
| GET | `/ambitos/{id}` | `AUDIT_SYS` | Obtener ámbito por ID |

### Usuario

| Método | Ruta | Auth | Descripción |
|---|---|---|---|
| POST | `/usuario/foto` | JWT | Subir foto de perfil del usuario autenticado |

---

## 12. Decisiones de diseño

Estas son las decisiones no obvias que un desarrollador debe conocer para mantener el proyecto.

### Seguridad por oscuridad en acceso restringido

Los recursos no accesibles por restricción de sección devuelven **HTTP 404**, no 403.
Esto impide que un atacante autenticado enumere la existencia de recursos a los que
no tiene acceso. El patrón se aplica en `find_by_id`, `update`, `delete` y todos
los métodos bulk de `ServidorService`.

### `_user` vs `user` en routers

En los routers, el resultado de la dependencia de autenticación se asigna a `_user`
(con guion bajo) cuando el handler no usa el objeto del usuario en el cuerpo:
el guion bajo indica "variable no usada". Se usa `user` (sin guion bajo) únicamente
en `servidor.py` porque todos sus handlers necesitan el objeto para calcular
`visible_section_ids(user, permiso)`.

### Dos DTOs de PATCH para servidor

`ServidorPatchRequest` es el DTO público (acepta camelCase desde el cliente, solo
campos editables por el usuario). `ServidorPatch` es el DTO interno completo
(incluye campos que solo el sistema puede establecer, como `hostname`, `arch`,
`kernel`). El servicio traduce entre ambos y puede construir un `ServidorPatch`
con campos SSH tras el re-probe.

### `model_dump(by_alias=False)` en actualizaciones ORM

Al aplicar un patch sobre un objeto ORM, es imprescindible usar `by_alias=False`
para obtener las claves en snake_case. Si se usara `by_alias=True`, los campos
camelCase no coincidirían con los atributos del ORM y la actualización quedaría
silenciosamente sin efecto.

### `model_fields_set` para distinción null-vs-ausente

En operaciones PATCH, un campo con valor `None` en el DTO puede significar "el
cliente quiere borrarlo" o "el cliente no lo ha enviado". Usar `model_fields_set`
permite distinguir ambos casos: solo están en el conjunto los campos que el cliente
incluyó explícitamente en el JSON, independientemente de si su valor es `None`.

### INSERT IGNORE en asociaciones en lote

Las operaciones de `add_servicios` usan `INSERT IGNORE` para que llamadas repetidas
con los mismos IDs sean idempotentes sin necesidad de consultar previamente cuáles
ya existen. Esto simplifica el cliente y evita condiciones de carrera en inserciones
concurrentes.

### Anti-N+1 en carga de servicios

`ServidorRepository` no carga los servicios asociados a cada servidor con una query
por servidor. En su lugar, hace una única query `WHERE servidor_id IN (...)` para
todos los servidores de la página y los agrupa en memoria. Esto reduce N+1 queries
a exactamente 2 queries por listado paginado.

### Compensación MinIO en `update_foto` de servidor

`ServidorService.update_foto` implementa una compensación parcial: si la actualización
en BD falla después de haber subido el fichero a MinIO, lo borra inmediatamente para
evitar ficheros huérfanos. `ServicioService.update_logo` no implementa esta compensación
(inconsistencia entre los dos servicios de foto del proyecto).

### Probe SSH: fatal en insert, silenciado en update

El probe SSH es **obligatorio** al crear un servidor: sin hostname/OS/arch/kernel el
registro carece de valor de diagnóstico. Al **actualizar** el DNS, el probe se
re-ejecuta para actualizar esos campos pero su fallo se silencia con
`except Exception: pass` sin ningún log. Si falla, el servidor queda con los datos
de diagnóstico del DNS anterior.

### Métricas: tres valores de retorno semánticamente distintos

`ServidorService.get_metrics` puede devolver:

- `None` → el `server_id` no existe en MariaDB o no es visible para el usuario.
- `[]` → el servidor existe y es visible, pero no hay métricas en la ventana solicitada.
- `list[dict]` → métricas encontradas.

El router convierte `None` en HTTP 404 y una lista (vacía o no) en HTTP 200.

### JWT con blocklist stateful

El logout es **stateful**: el JTI del token se almacena en Redis con un TTL igual al
tiempo de expiración residual. Esto corrige la limitación fundamental del JWT stateless
(un token robado es válido hasta su expiración). El coste es una consulta a Redis en
cada request autenticada.

### LDAP: patrón de doble bind

La autenticación LDAP usa dos conexiones:

1. **Bind de usuario**: verifica las credenciales directamente contra AD.
2. **Bind de cuenta de servicio**: busca los atributos del usuario (grupos AD, `objectGUID`).

El segundo bind es necesario porque en muchas configuraciones de AD los usuarios no tienen
permisos de lectura sobre los atributos de su propio directorio.

---

## 13. Notas para desarrolladores

### Esquema de BD

El esquema MariaDB no está gestionado por migraciones (Alembic u similar). Las tablas
deben existir previamente. El esquema original fue diseñado con la API Java y se reutiliza
sin modificaciones. Las tablas principales:

```sql
servidores, secciones, servicios, servidores_servicios,
grupos, usuarios_app,
permisos, ambitos,
grupo_permiso_global (grupo_id, permiso_id),
grupo_seccion (grupo_id, seccion_id, permiso_id)
```

### Convención de nombres en AD

La API usa `sam_account_name` (nombre de inicio de sesión corto, ej: `jdoe`) como
identificador de usuario, no el UPN completo (`jdoe@metrics.local`). El UPN se
construye en `LdapService._build_upn` con el dominio `@metrics.local` codificado
en duro. Si el dominio AD cambia, hay que actualizar ese método.

### Añadir un nuevo endpoint

1. Definir o extender el modelo en `models/`.
2. Añadir el método al repositorio en `repositories/` (con la query SQL/MongoDB).
3. Añadir el método al servicio en `services/` (con la lógica de negocio y orquestación).
4. Añadir el endpoint al router existente en `routers/` (o crear uno nuevo e incluirlo en `main.py`).
5. Si requiere un nuevo permiso, añadirlo al catálogo de la BD (`permisos` + `ambitos`).

### Singletons de infraestructura

Los clientes de MongoDB, MinIO y Redis son singletons a nivel de proceso, creados con
`@lru_cache`. Esto significa que una sola instancia se comparte en todas las requests.
No crear instancias adicionales de estos clientes fuera de los módulos `core/`.

### Logging

La aplicación configura el logging en `core/logging_config.py` antes de crear la app.
El nivel por defecto es `INFO`. Con `APP_DEBUG=true` se activa `DEBUG` en todas las
capas y el `DebugLoggingMiddleware` loga cabeceras y duración de cada request.

Los loggers de la aplicación usan `logging.getLogger(__name__)`, lo que produce nombres
como `services.servidor_service` o `routers.auth`. Para filtrar logs por capa en
producción, configurar el nivel por nombre de logger en la configuración de logging.

### Tests

El proyecto incluye una configuración de pytest con soporte async (`pytest-asyncio`).
Para ejecutar los tests:

```bash
pytest
```

Los tests de integración requieren los servicios externos (MariaDB, MongoDB, MinIO, Redis,
LDAP) accesibles y configurados en el entorno de test mediante variables de entorno o
un fichero `.env` de test.
