"""Local data profiles: datasets outside the repository, read and matched to the tables SQL names."""

PROFILE_VERSION = 1
DEFAULT_MAX_ROWS = 100_000
MAX_DATASETS = 2_000
MAX_FILES = 100_000
MAX_COLUMNS = 1_000


def clean_text(text: str) -> str:
    """Text safe to write as UTF-8: lone surrogates (bad JSON escapes, undecodable file names) become U+FFFD."""
    return text.encode("utf-8", "surrogatepass").decode("utf-8", "replace")
