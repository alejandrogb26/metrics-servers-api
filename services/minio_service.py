"""
Servicio de almacenamiento de objetos con MinIO.
Equivalente a MinioService.java.

Capa arquitectónica: Infraestructura / Servicio externo (MinIO/S3).

Responsabilidades:
    - Subir ficheros al bucket correspondiente (`upload`), creando el bucket
      si no existe (`_ensure_bucket`).
    - Generar URLs presignadas de acceso temporal a objetos almacenados
      (`get_presigned_url`).
    - Eliminar objetos de un bucket (`delete`).

Qué NO debe contener este fichero:
    - Generación de nombres de objeto (UUIDs, slugs). Eso es responsabilidad
      del servicio de dominio que llama a este servicio.
    - Persistencia del nombre de fichero en la base de datos. Eso pertenece a
      los repositorios (`update_logo`, `update_imagen`, `update_foto`).
    - Lógica de negocio sobre qué fichero subir o cuándo hacerlo.

Política de errores:
    Los tres métodos públicos tienen estrategias de error distintas:
    - `upload`: **propaga** `S3Error` al llamante (un fallo de subida es un
      error real que debe manejarse en la capa superior).
    - `get_presigned_url`: **silencia** `S3Error` devolviendo `None` (una URL
      no disponible es degradación aceptable; el cliente puede mostrar un
      placeholder).
    - `delete`: **silencia** `S3Error` sin retornar ningún valor (el objeto
      puede ya no existir; la operación es best-effort).

Buckets gestionados (nombres cargados desde configuración):
    - `BUCKET_USERS`      → fotos de perfil de usuarios (`usuarios_app`).
    - `BUCKET_SERVIDORES` → imágenes de servidores (`servidores`).
    - `BUCKET_SERVICIOS`  → logos de servicios (`servicios`).

Relaciones con otros módulos:
    - `core/config.py`              → `get_settings` para leer los nombres de
                                      bucket configurados.
    - `core/minio_client.py`        → `get_minio_client` proporciona el cliente
                                      MinIO singleton (cacheado con `@lru_cache`).
    - `services/auth_service.py`    → llama a `get_presigned_url` en el login
                                      para obtener la URL de la foto de perfil.
    - `services/usuario_service.py` → llama a `upload` y `get_presigned_url`
                                      para la gestión de fotos de perfil.
    - `services/servicio_service.py`→ llama a `upload` y `get_presigned_url`
                                      para la gestión de logos de servicio.
    - `services/servidor_service.py`→ llama a `upload` para la gestión de
                                      imágenes de servidor.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import io
import logging
from datetime import timedelta

from minio.error import S3Error

from core.config import get_settings
from core.minio_client import get_minio_client

log = logging.getLogger("api.minio")


class MinioService:
    """
    Fachada sobre el cliente MinIO para las operaciones de almacenamiento de
    objetos de la aplicación.

    El cliente MinIO (`_client`) se obtiene de `get_minio_client()`, que usa
    `@lru_cache` para devolver siempre la misma instancia durante la vida del
    proceso. Los nombres de bucket se exponen como atributos públicos de
    instancia (`BUCKET_USERS`, `BUCKET_SERVIDORES`, `BUCKET_SERVICIOS`) para
    que los llamantes los referencien sin conocer los valores de configuración.
    """

    def __init__(self) -> None:
        self._client = get_minio_client()
        s = get_settings()
        self.BUCKET_USERS = s.bucket_users
        self.BUCKET_SERVIDORES = s.bucket_servidores
        self.BUCKET_SERVICIOS = s.bucket_servicios

    def upload(self, bucket: str, object_name: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """
        Sube un objeto a MinIO, creando el bucket si no existe.

        Antes de la subida llama a `_ensure_bucket` para crear el bucket si
        aún no existe (creación lazy en el primer uso). El contenido se
        envuelve en `io.BytesIO` porque el SDK de MinIO espera un objeto
        file-like, no bytes directos.

        Este método no captura `S3Error`: cualquier fallo de subida (bucket
        inaccesible, error de red, cuota excedida) se propaga al llamante para
        que pueda manejarlo apropiadamente. Es el único método de esta clase
        que propaga errores al llamante.

        Args:
            bucket:       Nombre del bucket destino (usar las constantes
                          `BUCKET_USERS`, `BUCKET_SERVIDORES`, `BUCKET_SERVICIOS`).
            object_name:  Clave del objeto dentro del bucket (nombre del fichero
                          tal como quedará almacenado).
            data:         Contenido del fichero en bytes.
            content_type: MIME type del objeto. Por defecto
                          `application/octet-stream` (binario genérico).

        Lanza:
            `S3Error` si MinIO no puede completar la subida.
        """
        log.debug("MINIO upload bucket=%s key=%s size=%d type=%s",
                  bucket, object_name, len(data), content_type)
        self._ensure_bucket(bucket)
        self._client.put_object(
            bucket_name=bucket,
            object_name=object_name,
            data=io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        log.debug("MINIO upload ok bucket=%s key=%s", bucket, object_name)

    def get_presigned_url(self, bucket: str, object_name: str | None, expires: int = 3600) -> str | None:
        """
        Genera una URL presignada de acceso temporal a un objeto en MinIO.

        Una URL presignada permite al cliente descargar el objeto directamente
        desde MinIO sin autenticación adicional, durante el tiempo indicado por
        `expires`. Transcurrido ese tiempo la URL deja de ser válida.

        Devuelve `None` sin consultar MinIO si `object_name` es falsy (el
        objeto aún no ha sido subido, p.ej. usuario sin foto de perfil). Esto
        evita llamadas innecesarias a MinIO durante el login cuando el usuario
        no tiene foto.

        Captura `S3Error` silenciosamente y devuelve `None`: un fallo en la
        generación de URL (objeto no encontrado, MinIO temporalmente no
        disponible) se trata como degradación aceptable. El cliente puede
        mostrar un placeholder en lugar de un error.

        Args:
            bucket:      Nombre del bucket que contiene el objeto.
            object_name: Clave del objeto, o `None`/cadena vacía si no existe.
            expires:     Duración de la URL en segundos. Por defecto 3600 (1 hora).

        Retorna:
            URL presignada como string si tiene éxito, `None` si `object_name`
            está vacío o si se produce cualquier error con MinIO.
        """
        if not object_name:
            log.debug("MINIO presigned skip: object_name vacío bucket=%s", bucket)
            return None
        log.debug("MINIO presigned bucket=%s key=%s expires=%d", bucket, object_name, expires)
        try:
            url = self._client.presigned_get_object(
                bucket_name=bucket,
                object_name=object_name,
                expires=timedelta(seconds=expires),
            )
            log.debug("MINIO presigned ok bucket=%s key=%s", bucket, object_name)
            return url
        except S3Error as exc:
            log.debug("MINIO presigned error bucket=%s key=%s: %s", bucket, object_name, exc)
            return None

    def delete(self, bucket: str, object_name: str) -> None:
        """
        Elimina un objeto de un bucket. Operación best-effort: no propaga errores.

        Captura `S3Error` silenciosamente: si el objeto no existe (ya fue
        eliminado previamente) o MinIO no está disponible, la operación se
        completa sin error visible. Este comportamiento es intencional para
        que la limpieza de ficheros huérfanos no interrumpa flujos de borrado
        de entidades en la base de datos.

        El fallo se registra en `DEBUG` (no `WARNING`), por lo que no aparece
        en producción con nivel `INFO`. Si se necesita auditar las eliminaciones
        fallidas, habría que elevar este nivel.

        Args:
            bucket:      Nombre del bucket que contiene el objeto.
            object_name: Clave del objeto a eliminar.
        """
        log.debug("MINIO delete bucket=%s key=%s", bucket, object_name)
        try:
            self._client.remove_object(bucket, object_name)
            log.debug("MINIO delete ok bucket=%s key=%s", bucket, object_name)
        except S3Error as exc:
            log.debug("MINIO delete error bucket=%s key=%s: %s", bucket, object_name, exc)

    def _ensure_bucket(self, bucket: str) -> None:
        """
        Crea el bucket si no existe (creación lazy).

        Se llama antes de cada `upload` para garantizar que el bucket destino
        existe. En un sistema estable los buckets ya existen desde el primer uso
        y el coste de esta comprobación es una llamada adicional a MinIO por
        subida.

        No es segura ante concurrencia: si dos peticiones simultáneas llaman a
        `_ensure_bucket` con el mismo bucket inexistente, ambas pueden intentar
        crearlo a la vez. En la práctica esto no produce errores porque MinIO
        maneja la idempotencia de `make_bucket`, pero el comportamiento depende
        de la versión del servidor.

        Args:
            bucket: Nombre del bucket a garantizar.
        """
        exists = self._client.bucket_exists(bucket)
        if not exists:
            log.debug("MINIO ensure_bucket creando bucket=%s", bucket)
            self._client.make_bucket(bucket)
        else:
            log.debug("MINIO ensure_bucket existe bucket=%s", bucket)
