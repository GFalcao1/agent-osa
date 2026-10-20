"""Small, transport-neutral domain error hierarchy."""


class DomainError(Exception):
    """Base class for expected domain failures safe to translate at boundaries."""


class NotFound(DomainError):
    """A requested domain object is absent."""


class AccessDenied(DomainError):
    """The actor has no permission for the requested operation."""


class Conflict(DomainError):
    """Available evidence or requested state is contradictory."""


class UnsafePath(DomainError):
    """A filesystem path violates a policy boundary."""


class NotStable(DomainError):
    """A watched file has not yet met the stability criteria."""


class SensitiveFile(DomainError):
    """The file belongs to the sensitive class and must not be read or sent."""


class Unsupported(DomainError):
    """The file format has no implemented extraction."""


class ExtractionFailed(DomainError):
    """Content extraction failed (corrupt, protected, oversized or hostile file).

    The message names the kind of problem and never carries document content.
    """
