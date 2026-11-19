"""Exception types. `exit_code` is what a script exits with."""


class ArcheError(Exception):
    """Base class for every error the scripts raise on purpose."""

    exit_code = 1


class UsageError(ArcheError):
    """Bad arguments or an unusable environment."""

    exit_code = 2


class IntegrityError(ArcheError):
    """An input file is missing, malformed, or changed during the run."""

    exit_code = 3


class ParseError(ArcheError):
    """One source file could not be parsed. The file is skipped; the run continues."""
