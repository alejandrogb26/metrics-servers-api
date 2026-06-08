"""
Servicio de sondeo SSH para obtener información del servidor remoto.
Equivalente a ServidorProbeService.java.

Capa arquitectónica: Infraestructura / Servicio externo (SSH).

Responsabilidades:
    - Establecer una conexión SSH a un host remoto usando credenciales de
      servicio compartidas (configuradas en `Settings`).
    - Ejecutar cuatro comandos de diagnóstico en el host remoto y capturar
      sus salidas: `hostname`, `PRETTY_NAME` de `/etc/os-release`, `uname -m`
      y `uname -r`.
    - Devolver los resultados encapsulados en un objeto `ServidorInfo`.
    - Cerrar la conexión en todos los casos (éxito o fallo) mediante `finally`.

Qué NO debe contener este fichero:
    - Persistencia de datos. El resultado `ServidorInfo` lo guarda el llamante
      (`ServidorService`) a través del repositorio.
    - Lógica de negocio sobre cuándo sondar o qué hacer con el resultado.
    - Manejo de reintentos. Si el probe falla, eleva `RuntimeError` y delega
      la política de reintentos (o de fallo) al llamante.

Política de errores:
    Cualquier excepción durante la conexión o la ejecución de comandos se
    envuelve en `RuntimeError` y se re-lanza. El llamante decide si tratar
    el fallo como fatal (`ServidorService.insert` → `ProbeException` → HTTP 502)
    o silenciarlo (`ServidorService.update` → `except Exception: pass`).

Seguridad:
    - `AutoAddPolicy` acepta cualquier clave de host sin verificación (riesgo
      de MITM). Aceptable en redes internas de confianza; no recomendable en
      entornos expuestos a Internet.
    - Las credenciales SSH (`ssh_probe_user`, `ssh_probe_password`) son
      compartidas por todos los servidores del inventario. No hay rotación por
      servidor ni autenticación por clave pública.

Relaciones con otros módulos:
    - `core/config.py`               → `get_settings` para leer `ssh_probe_user`
                                       y `ssh_probe_password`.
    - `services/servidor_service.py` → único consumidor; usa `ask_server` en
                                       `insert`, `insert_bulk` y `update`.

Autor:
    Alejandro Gómez Blanco

Proyecto:
    Metrics Servers

Versión:
    1.0.0

Organización:
    Metrics Servers Project
"""

import paramiko

from core.config import get_settings


class ServidorInfo:
    """
    Resultado de un probe SSH exitoso sobre un servidor remoto.

    Contiene los cuatro datos de diagnóstico que el sistema almacena en la
    base de datos para cada servidor: nombre de host, sistema operativo
    (legible), arquitectura de CPU y versión del kernel.

    Es un objeto de datos plano (sin lógica) que actúa como barrera entre
    el protocolo de transporte SSH y el modelo de dominio MariaDB.
    """

    def __init__(self, hostname: str, pretty_os: str, arch: str, kernel: str) -> None:
        self.hostname = hostname
        self.pretty_os = pretty_os
        self.arch = arch
        self.kernel = kernel


class SshProbeService:
    """
    Ejecuta un conjunto fijo de comandos SSH en un host remoto y devuelve
    los resultados como `ServidorInfo`.

    Cada llamada a `ask_server` abre una nueva conexión SSH (sin pool de
    conexiones). En un insert individual esto es aceptable. En `insert_bulk`
    el llamante paraleliza las llamadas con `ThreadPoolExecutor` para evitar
    N conexiones en serie.

    Las credenciales de servicio se leen en el constructor; son compartidas
    por todos los servidores del inventario.
    """

    # Segundos máximos para establecer la conexión, el banner y la autenticación,
    # y para que cada comando remoto devuelva su salida.
    TIMEOUT = 5

    def __init__(self) -> None:
        s = get_settings()
        self._user = s.ssh_probe_user
        self._password = s.ssh_probe_password

    def ask_server(self, host: str) -> ServidorInfo:
        """
        Conecta por SSH al host indicado, ejecuta cuatro comandos de diagnóstico
        y devuelve los resultados como `ServidorInfo`.

        Comandos ejecutados (en orden):
            1. `hostname`                                  → nombre de host.
            2. `cat /etc/os-release | grep PRETTY_NAME …` → nombre legible del SO.
            3. `uname -m`                                  → arquitectura de CPU.
            4. `uname -r`                                  → versión del kernel.

        Cada comando pasa por `_exec`, que comprueba el código de salida y eleva
        `RuntimeError` si es distinto de 0. La conexión se cierra siempre en el
        bloque `finally`, tanto en caso de éxito como de fallo.

        `AutoAddPolicy` acepta automáticamente cualquier clave de host desconocida,
        lo que evita errores en el primer contacto con cada servidor pero elimina
        la protección contra ataques MITM.

        Args:
            host: Dirección IP o nombre DNS del servidor a sondar.

        Retorna:
            `ServidorInfo` con `hostname`, `pretty_os`, `arch` y `kernel`.

        Lanza:
            `RuntimeError` si la conexión falla, si la autenticación falla, si
            cualquiera de los comandos devuelve un código de salida distinto de 0,
            o si se supera `TIMEOUT` en cualquier fase.
        """
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                hostname=host,
                username=self._user,
                password=self._password,
                timeout=self.TIMEOUT,
                banner_timeout=self.TIMEOUT,
                auth_timeout=self.TIMEOUT,
            )
            hostname = self._exec(client, "hostname")
            pretty_os = self._exec(
                client,
                "cat /etc/os-release | grep PRETTY_NAME | cut -d= -f2 | tr -d '\"'",
            )
            arch = self._exec(client, "uname -m")
            kernel = self._exec(client, "uname -r")
            return ServidorInfo(hostname=hostname, pretty_os=pretty_os, arch=arch, kernel=kernel)
        except Exception as exc:
            raise RuntimeError(f"SSH probe falló para {host}: {exc}") from exc
        finally:
            client.close()

    @staticmethod
    def _exec(client: paramiko.SSHClient, cmd: str) -> str:
        """
        Ejecuta un comando en la sesión SSH y devuelve su salida estándar limpia.

        Espera a que el comando termine con `recv_exit_status()` antes de leer
        la salida. Si el código de salida es distinto de 0 considera que el
        comando falló y eleva `RuntimeError` con el código y el comando. No
        incluye la salida de error estándar (`stderr`) en el mensaje de error.

        El argumento `timeout` en `exec_command` limita el tiempo que el canal
        espera datos desde el host remoto, independientemente del `TIMEOUT`
        global de la conexión.

        Args:
            client: Conexión SSH activa (ya autenticada).
            cmd:    Comando shell a ejecutar en el host remoto.

        Retorna:
            Salida estándar del comando, decodificada en UTF-8 y sin espacios
            ni saltos de línea en los extremos.

        Lanza:
            `RuntimeError` si el comando devuelve un código de salida distinto
            de 0.
        """
        _, stdout, stderr = client.exec_command(cmd, timeout=SshProbeService.TIMEOUT)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError(f"Comando SSH falló ({exit_code}): {cmd}")
        return stdout.read().decode().strip()
