"""Column lineage for SQL files: a tokenizer, a parser for query expressions, scopes and derivation."""

COLUMNS_VERSION = 2  # 2: evidence is {path, line}; the text of each cited line is in `lines`
SUPPORTED_COLUMNS_VERSIONS = (1, 2)  # 1 carried the line text in every evidence as `quote`
MAX_STATEMENT_TOKENS = 200_000
MAX_DEPTH = 128
MAX_SELECT_ITEMS = 2_000
MAX_FILE_EDGES = 20_000
MAX_EDGES = 500_000
MAX_EXPRESSION_CHARS = 200
