"""Excepciones propias de GitKeeper."""


class GitKeeperError(Exception):
    """Error base: se muestra al usuario sin traceback."""


class ConfigError(GitKeeperError):
    """Configuración inválida o incompleta."""


class AuthError(GitKeeperError):
    """Credenciales ausentes o rechazadas por el proveedor."""


class ProviderError(GitKeeperError):
    """Error devuelto por la API de un proveedor (GitHub, GitLab, Bitbucket)."""

    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class NotFoundError(ProviderError):
    """El repositorio o recurso no existe (o no hay permisos para verlo)."""


class UnsupportedOperation(GitKeeperError):
    """La operación no está disponible en este proveedor."""


class RepoResolutionError(GitKeeperError):
    """No se pudo identificar un único repositorio a partir del texto dado."""


class AIError(GitKeeperError):
    """Error del proveedor de IA."""
