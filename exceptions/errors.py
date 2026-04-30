class DaoException(Exception):
    """Error en la capa de acceso a datos. Equivalente a DaoException.java."""
    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class ProbeException(Exception):
    """Error en el sondeo SSH de servidores. Equivalente a ProbeException.java."""
    def __init__(self, message: str, cause: Exception | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class NotFoundException(Exception):
    """Recurso no encontrado."""
    pass


class ValidationException(Exception):
    """Error de validación de negocio."""
    pass
