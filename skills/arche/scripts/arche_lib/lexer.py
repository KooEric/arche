"""Mask comments and string bodies so simple patterns can scan source text safely.

This is not a parser. It keeps every character position and every newline, and
replaces what must not be mistaken for code with spaces. Known limits: JSX text
with unbalanced brackets, and JavaScript regex literals in unusual positions,
can leave the bracket count unbalanced. Callers treat that as `degraded`.
"""
from __future__ import annotations

from bisect import bisect_right
from typing import Dict, List, NamedTuple, Tuple

OPENERS = "([{"
CLOSERS = ")]}"
_WORD_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_$")
_REGEX_AFTER_CHARS = frozenset("(,=:[!&|?{;+-*%~^")
_REGEX_AFTER_WORDS = frozenset(
    {"return", "typeof", "case", "in", "of", "delete", "void", "throw", "new", "yield", "await", "else", "do"}
)
_STRING_AFTER_WORDS = frozenset(
    {"from", "import", "export", "return", "case", "typeof", "in", "of", "default", "await",
     "yield", "else", "new", "throw", "delete", "void", "extends", "require"}
)


class Rules(NamedTuple):
    templates: bool          # `...${code}...` strings
    regex: bool              # /regex/ literals
    word_quote_guard: bool   # a quote right after a word is text (JSX: Don't), not a string
    nested_comments: bool    # /* /* */ */
    interpolation: bool      # '${code}' inside quoted strings
    triple_quotes: bool      # '''multi-line'''
    raw_prefix: bool         # r'no escapes'


JS = Rules(templates=True, regex=True, word_quote_guard=True, nested_comments=False,
           interpolation=False, triple_quotes=False, raw_prefix=False)
DART = Rules(templates=False, regex=False, word_quote_guard=False, nested_comments=True,
             interpolation=True, triple_quotes=True, raw_prefix=True)


class Masked(NamedTuple):
    text: str                      # same length as the source
    strings: Dict[int, str]        # offset of an opening quote -> raw string body
    line_starts: Tuple[int, ...]   # offset where each line begins
    line_depths: Tuple[int, ...]   # bracket nesting at the start of each line
    balanced: bool                 # brackets never went negative and ended at zero


def line_of(masked: Masked, offset: int) -> int:
    """1-based line number of an offset."""
    return bisect_right(masked.line_starts, offset)


def depth_at(masked: Masked, offset: int) -> int:
    """Bracket nesting just before `offset`."""
    line = line_of(masked, offset) - 1
    depth = masked.line_depths[line]
    for char in masked.text[masked.line_starts[line]:offset]:
        if char in OPENERS:
            depth += 1
        elif char in CLOSERS:
            depth -= 1
    return depth


class _Scanner:
    """One pass over the source. Mutable on purpose; nothing outside `mask` sees it."""

    def __init__(self, source: str, rules: Rules) -> None:
        self.src = source
        self.end = len(source)
        self.rules = rules
        self.out: List[str] = list(source)
        self.strings: Dict[int, str] = {}
        self.pos = 0

    def blank(self, start: int, stop: int) -> None:
        for index in range(start, min(stop, self.end)):
            if self.src[index] != "\n":
                self.out[index] = " "

    def code(self, in_interpolation: bool) -> None:
        """Scan code. Inside `${...}` return at the brace that closes it."""
        depth = 0
        while self.pos < self.end:
            char = self.src[self.pos]
            pair = self.src[self.pos:self.pos + 2]
            if pair == "//":
                self.line_comment()
            elif pair == "/*":
                self.block_comment()
            elif char in "'\"":
                self.quote()
            elif char == "`" and self.rules.templates:
                self.string(self.pos, 1, "`", multiline=True, interpolate=True, escapes=True)
            elif char == "/" and self.rules.regex and self.regex_allowed():
                self.regex()
            elif in_interpolation and char == "}" and depth == 0:
                return
            else:
                if in_interpolation and char in "{}":
                    depth += 1 if char == "{" else -1
                self.pos += 1

    def line_comment(self) -> None:
        stop = self.src.find("\n", self.pos)
        stop = self.end if stop == -1 else stop
        self.blank(self.pos, stop)
        self.pos = stop

    def block_comment(self) -> None:
        start = self.pos
        depth = 1
        index = self.pos + 2
        while index < self.end and depth:
            pair = self.src[index:index + 2]
            if pair == "*/":
                depth -= 1
                index += 2
            elif pair == "/*" and self.rules.nested_comments:
                depth += 1
                index += 2
            else:
                index += 1
        self.blank(start, index)
        self.pos = index

    def previous_word(self, index: int) -> str:
        stop = index
        while index > 0 and self.src[index - 1] in _WORD_CHARS:
            index -= 1
        return self.src[index:stop]

    def quote(self) -> None:
        start = self.pos
        char = self.src[start]
        word = self.previous_word(start)
        raw = self.rules.raw_prefix and word == "r"
        if self.rules.word_quote_guard and word and word not in _STRING_AFTER_WORDS:
            self.pos += 1
            return
        triple = self.rules.triple_quotes and self.src[start:start + 3] == char * 3
        closer = char * 3 if triple else char
        self.string(start, len(closer), closer, multiline=triple,
                    interpolate=self.rules.interpolation and not raw, escapes=not raw)

    def string(self, start: int, open_len: int, closer: str, multiline: bool,
               interpolate: bool, escapes: bool) -> None:
        index = start + open_len
        segment = index
        while index < self.end:
            char = self.src[index]
            if escapes and char == "\\":
                index += 2
            elif self.src.startswith(closer, index):
                break
            elif char == "\n" and not multiline:
                break
            elif interpolate and self.src[index:index + 2] == "${":
                self.blank(segment, index + 2)
                self.pos = index + 2
                self.code(in_interpolation=True)
                self.blank(self.pos, self.pos + 1)
                index = segment = self.pos + 1
            else:
                index += 1
        index = min(index, self.end)
        self.blank(segment, index)
        self.strings[start] = self.src[start + open_len:index]
        self.pos = index + len(closer) if self.src.startswith(closer, index) else index

    def regex_allowed(self) -> bool:
        index = self.pos
        while index > 0 and self.out[index - 1] in " \t\r\n":
            index -= 1
        if index == 0:
            return True
        previous = self.out[index - 1]
        if previous in _REGEX_AFTER_CHARS or self.src[index - 2:index] == "=>":
            return True
        return previous in _WORD_CHARS and self.previous_word(index) in _REGEX_AFTER_WORDS

    def regex(self) -> None:
        index = self.pos + 1
        in_class = False
        while index < self.end and self.src[index] != "\n":
            char = self.src[index]
            if char == "\\":
                index += 2
                continue
            if char == "/" and not in_class:
                self.blank(self.pos + 1, index)
                self.pos = index + 1
                return
            in_class = (char == "[") or (in_class and char != "]")
            index += 1
        self.pos += 1  # no closing slash on this line: it was a division sign


def _line_depths(text: str) -> Tuple[Tuple[int, ...], Tuple[int, ...], bool]:
    starts = [0]
    depths = [0]
    depth = 0
    lowest = 0
    for index, char in enumerate(text):
        if char in OPENERS:
            depth += 1
        elif char in CLOSERS:
            depth -= 1
            lowest = min(lowest, depth)
        elif char == "\n":
            starts.append(index + 1)
            depths.append(depth)
    return tuple(starts), tuple(depths), depth == 0 and lowest == 0


def mask(source: str, rules: Rules) -> Masked:
    scanner = _Scanner(source, rules)
    scanner.code(in_interpolation=False)
    text = "".join(scanner.out)
    starts, depths, balanced = _line_depths(text)
    return Masked(text=text, strings=scanner.strings, line_starts=starts,
                  line_depths=depths, balanced=balanced)
