from sqlmodel import Session, select
from models.usuario import UsuarioApp


class UsuarioRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def find_by_username(self, username: str) -> UsuarioApp | None:
        stmt = select(UsuarioApp).where(UsuarioApp.username == username)
        return self.session.exec(stmt).first()

    def find_by_ad_object_id(self, ad_object_id: str) -> UsuarioApp | None:
        stmt = select(UsuarioApp).where(UsuarioApp.ad_object_id == ad_object_id)
        return self.session.exec(stmt).first()

    def insert(self, usuario: UsuarioApp) -> UsuarioApp:
        try:
            self.session.add(usuario)
            self.session.commit()
            self.session.refresh(usuario)
            return usuario
        except Exception:
            self.session.rollback()
            raise

    def update_foto(self, username: str, nombre_archivo: str) -> None:
        usuario = self.find_by_username(username)
        if usuario is None:
            return
        try:
            usuario.foto_perfil = nombre_archivo
            self.session.add(usuario)
            self.session.commit()
        except Exception:
            self.session.rollback()
            raise
