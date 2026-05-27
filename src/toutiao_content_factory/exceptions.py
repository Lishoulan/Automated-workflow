class ContentFactoryError(Exception):
    """Base application exception."""


class LowQualityAbortException(ContentFactoryError):
    """Raised when a topic is too weak to justify the full agent workflow."""


class InputNotFoundError(ContentFactoryError):
    """Raised when no supported input sources are found."""


class UnsupportedFileTypeError(ContentFactoryError):
    """Raised when an input file type is not supported."""


class AIRequestError(ContentFactoryError):
    """Raised when the model request fails."""


class AIResponseFormatError(ContentFactoryError):
    """Raised when the model response cannot be parsed."""


class PlatformAuthError(ContentFactoryError):
    """Raised when a required publish platform has an invalid login session."""

    def __init__(
        self,
        message: str,
        *,
        blocking_platforms: list[str] | None = None,
        platform_health_path: str = "",
    ) -> None:
        super().__init__(message)
        self.blocking_platforms = list(blocking_platforms or [])
        self.platform_health_path = str(platform_health_path or "")
