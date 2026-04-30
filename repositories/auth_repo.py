from sqlmodel import Session, select
from models.grupo import Grupo
from models.permission_map import PermissionMap
from models.common import SessionResponse
from models.usuario import UsuarioApp


class AuthRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def build_session(
        self,
        username: str,
        display_name: str | None,
        mail: str | None,
        grupo: Grupo,
    ) -> tuple[SessionResponse, str | None]:
        """Construye la respuesta de sesión con permisos incluidos."""
        from repositories.grupo_repo import GrupoRepository
        repo = GrupoRepository(self.session)

        global_perms = repo.get_global_permission_names(grupo.id)  # type: ignore[arg-type]
        section_perms = repo.get_section_permission_names(grupo.id)  # type: ignore[arg-type]

        pmap = PermissionMap[str](
            global_perms=global_perms,
            sections=section_perms,
        )

        foto_perfil = self._get_foto_perfil(username)

        return SessionResponse(
            username=username,
            display_name=display_name,
            email=mail,
            grupo={
                "id": grupo.id,
                "nombre": grupo.nombre,
                "superadmin": grupo.superadmin,
            },
            # model_dump(by_alias=True) produce globalPerms en lugar de global_perms
            permisos=pmap.model_dump(by_alias=True),
            # url_foto se asigna en el service tras resolver la URL de MinIO
        ), foto_perfil

    def _get_foto_perfil(self, username: str) -> str | None:
        usuario = self.session.exec(
            select(UsuarioApp).where(UsuarioApp.username == username)
        ).first()
        return usuario.foto_perfil if usuario else None
