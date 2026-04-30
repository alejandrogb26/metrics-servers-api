"""
Servicio de sondeo SSH para obtener información del servidor remoto.
Equivalente a ServidorProbeService.java.
"""

import paramiko

from core.config import get_settings


class ServidorInfo:
    def __init__(self, hostname: str, pretty_os: str, arch: str, kernel: str) -> None:
        self.hostname = hostname
        self.pretty_os = pretty_os
        self.arch = arch
        self.kernel = kernel


class SshProbeService:
    TIMEOUT = 5

    def __init__(self) -> None:
        s = get_settings()
        self._user = s.ssh_probe_user
        self._password = s.ssh_probe_password

    def ask_server(self, host: str) -> ServidorInfo:
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
        _, stdout, stderr = client.exec_command(cmd, timeout=SshProbeService.TIMEOUT)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            raise RuntimeError(f"Comando SSH falló ({exit_code}): {cmd}")
        return stdout.read().decode().strip()
