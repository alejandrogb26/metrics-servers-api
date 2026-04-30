# metrics-servers — API Python (FastAPI + SQLModel)

Reimplementación en Python 3.12+ de la API REST original escrita en Java (Jakarta EE / TomEE).

## Stack

| Componente | Java (original) | Python (este proyecto) |
|---|---|---|
| Framework web | JAX-RS / Apache CXF | **FastAPI** |
| ORM / BD | JDBC manual | **SQLModel** (SQLAlchemy) |
| BD relacional | MariaDB | MariaDB (mismo esquema) |
| BD métricas | MongoDB | MongoDB (pymongo) |
| Almacenamiento objetos | MinIO SDK Java | **MinIO** (minio-py) |
| Autenticación | LDAP / AD (javax.naming) | **ldap3** |
| JWT | jjwt | **python-jose** |
| SSH probe | SSHJ | **paramiko** |
| Config | dotenv-java | **pydantic-settings** |

---

## Estructura del proyecto

```
metrics-servers/
├── main.py                        # Entrypoint FastAPI
├── pyproject.toml                 # Dependencias
├── .env.example                   # Variables de entorno de ejemplo
│
├── core/
│   ├── config.py                  # Settings (pydantic-settings)
│   ├── database.py                # Engine SQLModel + get_session dependency
│   ├── mongo.py                   # MongoClient singleton
│   ├── minio_client.py            # MinioClient singleton
│   ├── security.py                # JWT: crear y validar tokens
│   └── dependencies.py           # get_current_user, require_permission (auth middleware)
│
├── models/
│   ├── ambito.py                  # Ambito (table) + AmbitoRead
│   ├── permiso.py                 # Permiso (table) + PermisoRead
│   ├── seccion.py                 # Seccion (table) + schemas
│   ├── servicio.py                # Servicio (table) + schemas
│   ├── servidor.py                # Servidor (table) + ServidorCreate/Read/Patch
│   ├── grupo.py                   # Grupo (table) + schemas
│   ├── usuario.py                 # UsuarioApp (table)
│   ├── permission_map.py          # PermissionMap[T] genérico
│   └── common.py                  # PagedResponse[T], BulkResult, LoginRequest/Response
│
├── repositories/
│   ├── ambito_repo.py
│   ├── permiso_repo.py
│   ├── seccion_repo.py
│   ├── servicio_repo.py
│   ├── servidor_repo.py           # Con lógica LEFT JOIN servicios
│   ├── grupo_repo.py              # Permisos globales + por sección, bulk sin N+1
│   ├── usuario_repo.py
│   ├── auth_repo.py               # build_session
│   └── mongo_repo.py              # Métricas MongoDB
│
├── services/
│   ├── ldap_service.py            # Autenticación LDAP / AD
│   ├── auth_service.py            # login(), syncUsuarioApp()
│   ├── minio_service.py           # upload, presigned URL
│   ├── ssh_probe_service.py       # Probe SSH al registrar servidores
│   ├── servidor_service.py        # CRUD + bulk + foto + métricas
│   ├── servicio_service.py
│   ├── seccion_service.py
│   ├── grupo_service.py           # GrupoService + GrupoPermisosService
│   ├── usuario_service.py         # Foto de perfil
│   ├── ambito_service.py
│   └── permiso_service.py
│
├── routers/
│   ├── health.py                  # GET /health/status (público)
│   ├── auth.py                    # POST /auth/login
│   ├── servidor.py                # /servidor  (CRUD, bulk, foto, métricas)
│   ├── servicio.py                # /servicio  (CRUD)
│   ├── seccion.py                 # /seccion   (CRUD)
│   ├── grupo.py                   # /grupos    (CRUD, superadmin)
│   ├── grupo_permisos.py          # /grupos/{id}/permisos (PUT/PATCH)
│   ├── permiso.py                 # /permisos  (solo lectura)
│   ├── ambito.py                  # /ambitos   (solo lectura)
│   └── usuario.py                 # /usuario/foto
│
└── exceptions/
    ├── errors.py                  # DaoException, ProbeException, etc.
    └── handlers.py                # Manejadores globales FastAPI
```

---

## Instalación y arranque

### 1. Requisitos

- Python 3.12+
- MariaDB corriendo con el esquema existente
- MongoDB corriendo
- MinIO corriendo
- Servidor LDAP/AD accesible

### 2. Instalar dependencias

```bash
# Con pip
pip install -e .

# Con uv (recomendado)
uv sync
```

### 3. Configurar variables de entorno

```bash
cp .env.example .env
# Editar .env con los valores reales
```

### 4. Arrancar el servidor

```bash
# Desarrollo (con recarga automática)
uvicorn main:app --reload --host 0.0.0.0 --port 8080

# O directamente
python main.py
```

---

## Endpoints

La documentación interactiva está disponible en:

- **Swagger UI**: `http://localhost:8080/docs`
- **ReDoc**: `http://localhost:8080/redoc`

### Resumen de rutas

| Método | Ruta | Permiso requerido | Descripción |
|--------|------|-------------------|-------------|
| GET | `/health/status` | — | Estado de la BD |
| POST | `/auth/login` | — | Login LDAP → JWT |
| GET | `/servidor` | `AUDIT_SERV` | Listar servidores (paginado) |
| GET | `/servidor/{id}` | `AUDIT_SERV` | Obtener servidor |
| POST | `/servidor/bulk` | `MODIFY_SERV` | Crear servidores en lote |
| PATCH | `/servidor/{id}` | `MODIFY_SERV` | Actualizar servidor |
| DELETE | `/servidor/{id}` | `MODIFY_SERV` | Eliminar servidor |
| DELETE | `/servidor/bulk` | `MODIFY_SERV` | Eliminar en lote |
| POST | `/servidor/{id}/servicios` | `MODIFY_SERV` | Vincular servicios |
| DELETE | `/servidor/{id}/servicios` | `MODIFY_SERV` | Desvincular servicios |
| POST | `/servidor/{id}/foto` | `MODIFY_SERV` | Subir imagen |
| GET | `/servidor/{serverId}/metrics` | `AUDIT_SERV` | Métricas MongoDB |
| GET | `/servicio` | `AUDIT_SERV` | Listar servicios |
| GET | `/servicio/{id}` | `AUDIT_SERV` | Obtener servicio |
| POST | `/servicio` | `MODIFY_SERV` | Crear servicio |
| PATCH | `/servicio/{id}` | `MODIFY_SERV` | Actualizar servicio |
| DELETE | `/servicio/{id}` | `MODIFY_SERV` | Eliminar servicio |
| GET | `/seccion` | `AUDIT_SERV` | Listar secciones |
| GET | `/seccion/{id}` | `AUDIT_SERV` | Obtener sección |
| POST | `/seccion` | `MODIFY_SERV` | Crear sección |
| PATCH | `/seccion/{id}` | `MODIFY_SERV` | Actualizar sección |
| DELETE | `/seccion/{id}` | `MODIFY_SERV` | Eliminar sección |
| GET | `/grupos` | `AUDIT_USER` | Listar grupos |
| GET | `/grupos/{id}` | `AUDIT_USER` | Obtener grupo |
| POST | `/grupos` | `MODIFY_USER` | Crear grupos en lote |
| PATCH | `/grupos/{id}` | `MODIFY_USER` | Actualizar grupo |
| PATCH | `/grupos/{id}/superadmin` | Solo superadmin | Cambiar superAdmin |
| DELETE | `/grupos` | `MODIFY_USER` | Eliminar grupos en lote |
| PUT | `/grupos/{id}/permisos` | `MODIFY_USER` | Reemplazar todos los permisos |
| PATCH | `/grupos/{id}/permisos/global` | `MODIFY_USER` | Permisos globales (add/remove) |
| PUT | `/grupos/{id}/permisos/secciones/{secId}` | `MODIFY_USER` | Permisos de sección |
| PATCH | `/grupos/{id}/permisos/secciones/{secId}` | `MODIFY_USER` | Permisos sección (add/remove) |
| GET | `/permisos` | `AUDIT_USER` | Listar permisos |
| GET | `/permisos/{id}` | `AUDIT_USER` | Obtener permiso |
| GET | `/ambitos` | `AUDIT_SYS` | Listar ámbitos |
| GET | `/ambitos/{id}` | `AUDIT_SYS` | Obtener ámbito |
| POST | `/usuario/foto` | JWT válido | Subir foto de perfil |

---

## Autenticación y autorización

El sistema usa **JWT Bearer tokens**. El flujo es:

1. `POST /auth/login` con `username` + `password` → devuelve `token`
2. Incluir en cada petición: `Authorization: Bearer <token>`

### Permisos

Los permisos tienen formato `NOMBRE_AMBITO` (p.ej. `AUDIT_SERV`, `MODIFY_USER`).
Se pueden asignar de dos formas a un grupo:

- **Globales**: aplican a todos los recursos del sistema
- **Por sección**: aplican solo a los recursos de esa sección

Los **superadmins** tienen acceso total sin necesidad de permisos explícitos.

---

## Esquema de BD (MariaDB)

Las tablas principales que debe tener el esquema:

```sql
servidores, secciones, servicios, servidores_servicios,
grupos, usuarios_app,
permisos, ambitos,
grupo_permiso_global (grupoId, permisoId),
grupo_seccion (grupoId, seccionId, permisoId)
```
