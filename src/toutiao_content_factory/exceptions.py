class ContentFactoryError(Exception):
    """Base application exception."""


class InputNotFoundError(ContentFactoryError):
    """Raised when no supported input sources are found."""


class UnsupportedFileTypeError(ContentFactoryError):
    """Raised when an input file type is not supported."""


class AIRequestError(ContentFactoryError):
    """Raised when the model request fails."""


class AIResponseFormatError(ContentFactoryError):
    """Raised when the model response cannot be parsed."""
