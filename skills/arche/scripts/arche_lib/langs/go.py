"""Conservative Go package imports and platform constraints."""
from __future__ import annotations

import posixpath
import re
from typing import NamedTuple

from ..lexer import Rules, depth_at, line_of, mask

_RULES = Rules(True, False, False, False, False, False, False)
_OS = set('aix android darwin dragonfly freebsd illumos ios js linux netbsd openbsd plan9 solaris wasip1 windows zos'.split())
_ARCH = set('386 amd64 amd64p32 arm arm64 loong64 mips mipsle mips64 mips64le ppc64 ppc64le riscv64 s390x sparc64 wasm'.split())


def conditions(path, text):
    stem = posixpath.basename(path)[:-3]
    parts = stem.split('_')
    if parts[-1] == 'test':
        parts.pop()
    result = {}
    if len(parts) > 1 and parts[-1] in _ARCH:
        result['arch'] = parts.pop()
    if len(parts) > 1 and parts[-1] in _OS:
        result['os'] = parts[-1]
    # Build constraints are only valid in the leading comment area.
    prefix = re.split(r'(?m)^\s*package\s+', text, maxsplit=1)[0]
    modern = re.findall(r'(?m)^\s*//go:build\s+(.+?)\s*$', prefix)
    legacy = re.findall(r'(?m)^\s*//\s*\+build\s+(.+?)\s*$', prefix)
    if modern:
        result['go_build'] = modern
    if legacy:
        result['plus_build'] = legacy
    if posixpath.basename(path).endswith('_test.go'):
        result['test'] = True
    return result


def extract(path, text):
    masked = mask(text, _RULES)
    imports = []
    pattern = re.compile(r'(?m)^\s*import\s*(\(|(?:[\w.]+\s+)?["`])')
    for match in pattern.finditer(masked.text):
        if depth_at(masked, match.start()) != 0:
            continue
        start = match.start(1)
        if masked.text[start] == '(':
            end = masked.text.find(')', start + 1)
            if end < 0:
                continue
            start += 1
        else:
            end = masked.text.find('\n', start)
            if end < 0:
                end = len(text)
        for item in re.finditer(r'(?m)(?:\b([A-Za-z_]\w*)\s+|(?<!\S)([.])\s+)?(["`])', masked.text[start:end]):
            quote_offset = start + item.start(3)
            spec = masked.strings.get(quote_offset)
            if spec is None:
                continue
            line = line_of(masked, quote_offset)
            alias = item.group(1) or item.group(2)
            imports.append({'spec': spec, 'line': line, 'quote': text.splitlines()[line - 1].strip(),
                            'names': [], 'attrs': {'alias': alias} if alias else {}})
    symbols = []
    for m in re.finditer(r'(?m)^\s*func\s+(?:\([^\n)]*\)\s*)?([A-Za-z_]\w*)\s*\(', masked.text):
        if depth_at(masked, m.start()) == 0:
            name = m.group(1)
            symbols.append({'name': name, 'kind': 'function', 'line': line_of(masked, m.start(1)), 'exported': name[0].isupper()})
    return {'imports': imports, 'symbols': symbols, 'build_constraints': conditions(path, text)}


class Context(NamedTuple):
    modules: dict
    parsed: frozenset
    all_paths: frozenset
    constraints: dict


def build_context(parsed_paths, all_paths, read_path):
    modules = {}
    for path in sorted(all_paths):
        if posixpath.basename(path) != 'go.mod':
            continue
        match = re.search(r'(?m)^\s*module\s+(?:"([^"\n]+)"|([^\s/][^\s]*))', read_path(path) or '')
        if match:
            modules[posixpath.dirname(path)] = match.group(1) or match.group(2)
    return Context(modules, frozenset(parsed_paths), frozenset(all_paths),
                   {p: conditions(p, read_path(p) or '') for p in parsed_paths})


def _within(path, root):
    return not root or path == root or path.startswith(root + '/')


def resolve(imp, importer, ctx):
    spec = imp['spec']
    roots = [root for root in ctx.modules if _within(importer, root)]
    if not roots:
        return [{'status': 'unresolved', 'spec': spec, 'reason': 'module_not_found'}]
    root = max(roots, key=len)
    module = ctx.modules[root]
    if spec != module and not spec.startswith(module + '/'):
        return [{'status': 'external', 'package': spec}]
    suffix = spec[len(module):].lstrip('/')
    target = posixpath.join(root, suffix) if suffix else root
    # A nested go.mod creates a separate module, even when its path shares a prefix.
    if any(other != root and _within(target, other) and _within(other, root) for other in ctx.modules):
        return [{'status': 'external', 'package': spec}]
    candidates = sorted(p for p in ctx.parsed if posixpath.dirname(p) == target and not p.endswith('_test.go'))
    if not candidates:
        skipped = any(posixpath.dirname(p) == target and p.endswith('.go') for p in ctx.all_paths)
        return [{'status': 'unresolved', 'spec': spec, 'reason': 'target_skipped' if skipped else 'module_not_found'}]
    return [{'status': 'inferred', 'to': p, 'names': [], 'rule': 'go_package',
             'target_build_constraints': ctx.constraints[p]} for p in candidates]
