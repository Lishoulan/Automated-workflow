"""Custom exception hierarchy for AI Content Factory.

All application-specific exceptions inherit from ContentFactoryError,
organized by category for structured error handling.
"""
from __future__ import annotations


# ========================================================================
# Base
# ========================================================================

class ContentFactoryError(Exception):
    """Base application exception."""


# ========================================================================
# Content Quality
# ========================================================================

class ContentQualityError(ContentFactoryError):
    """Content quality does not meet standards."""

class LowQualityAbortException(ContentQualityError):
    """Topic quality is too low to justify the full agent workflow."""


# ========================================================================
# Content Safety
# ========================================================================

class ContentSafetyError(ContentFactoryError):
    """Content safety violation detected."""

class SensitiveWordError(ContentSafetyError):
    """Sensitive or prohibited word detected in content."""


# ========================================================================
# Input / IO
# ========================================================================

class InputNotFoundError(ContentFactoryError):
    """No supported input sources found."""

class UnsupportedFileTypeError(ContentFactoryError):
    """Input file type is not supported."""


# ========================================================================
# AI / Model
# ========================================================================

class AIRequestError(ContentFactoryError):
    """Model API request failed."""

class AIResponseFormatError(AIRequestError):
    """Model response could not be parsed."""


# ========================================================================
# Publishing
# ========================================================================

class PublishError(ContentFactoryError):
    """Publishing operation failed."""

class RPAError(PublishError):
    """Browser automation (RPA) operation failed."""

class RPAElementNotFoundError(RPAError):
    """Target page element not found during RPA automation."""

class PlatformAuthError(PublishError):
    """Platform authentication session is invalid or expired."""

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


# ========================================================================
# Configuration
# ========================================================================

class ConfigError(ContentFactoryError):
    """Configuration is invalid or missing."""


# ========================================================================
# Database
# ========================================================================

class DatabaseError(ContentFactoryError):
    """Database operation failed."""


# ========================================================================
# Network
# ========================================================================

class NetworkError(ContentFactoryError):
    """Network request failed."""

class NetworkTimeoutError(NetworkError):
    """Network request timed out."""

class NetworkConnectionError(NetworkError):
    """Could not establish network connection."""

class SourceUnavailableError(NetworkError):
    """Content source is persistently unavailable."""


# ========================================================================
# WeChat
# ========================================================================

class WechatAuthError(PublishError):
    """WeChat API authentication failed (invalid appid/appsecret)."""


class SessionExpiredException(PublishError):
    """WeChat MP browser login session has expired and needs re-authentication."""
