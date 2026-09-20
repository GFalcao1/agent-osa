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
