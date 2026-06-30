"""Lean 4 source utilities for sorry-tracker.

Provides the comment-aware line scanner used to detect `sorry`/`admit` without
false positives, and import resolution that maps a dotted Lean module name to a
file on disk. The comment parser mirrors the one shared by the sibling
lean-review-workflow / lean-summary-workflow repos.
"""

import os
import re
from typing import List, Optional, Tuple


def is_in_comment(line: str, nesting_depth: int) -> Tuple[bool, int]:
    """Determines if a line's code content is entirely within comments.

    Handles Lean 4's nested ``/- ... -/`` block comments and ``--`` line
    comments.

    Returns (line_has_no_code_outside_comments, new_nesting_depth).
    """
    stripped = line.strip()

    if nesting_depth == 0 and stripped.startswith('--'):
        return True, 0

    has_code = False
    i = 0
    while i < len(stripped):
        if i + 1 < len(stripped):
            pair = stripped[i:i + 2]
            if pair == '/-':
                nesting_depth += 1
                i += 2
                continue
            if pair == '-/' and nesting_depth > 0:
                nesting_depth -= 1
                i += 2
                continue
            if pair == '--' and nesting_depth == 0:
                break  # rest of line is a single-line comment

        if nesting_depth == 0 and not stripped[i].isspace():
            has_code = True
        i += 1

    return not has_code, nesting_depth


def strip_comments(line: str, nesting_depth: int) -> Tuple[str, int]:
    """Return the code portion of a line, with Lean 4 comments removed.

    Strips nested ``/- ... -/`` block comments (honoring the incoming depth)
    and a trailing ``--`` line comment, while leaving string-literal contents
    intact. String awareness prevents a ``--`` or ``/-`` inside a double-quoted
    string from being mistaken for a comment delimiter.

    Use this before scanning a line for keywords so that a keyword mentioned in
    a comment is not matched against real code. Returns
    (code_only_text, new_nesting_depth).
    """
    out = []
    i = 0
    n = len(line)
    in_string = False
    while i < n:
        ch = line[i]
        if in_string:
            out.append(ch)
            if ch == '"' and (i == 0 or line[i - 1] != '\\'):
                in_string = False
            i += 1
            continue
        if nesting_depth == 0:
            if ch == '"':
                in_string = True
                out.append(ch)
                i += 1
                continue
            pair = line[i:i + 2]
            if pair == '/-':
                nesting_depth += 1
                i += 2
                continue
            if pair == '--':
                break  # rest of the line is a single-line comment
            out.append(ch)
            i += 1
            continue
        # Inside a block comment (nesting_depth > 0): consume until it closes.
        pair = line[i:i + 2]
        if pair == '/-':
            nesting_depth += 1
            i += 2
            continue
        if pair == '-/':
            nesting_depth -= 1
            i += 2
            continue
        i += 1
    return ''.join(out), nesting_depth


def scrub_line(line: str, nesting_depth: int, in_string: bool) -> Tuple[str, int, bool]:
    """Return the scannable code of a line, with Lean comments **and string
    literal contents** removed, threading both block-comment nesting and
    open-string state across lines.

    ``strip_comments`` deliberately preserves string contents (so a ``--`` or
    ``/-`` inside a string is not mistaken for a comment). For *keyword*
    scanning that is unsound: a ``"... sorry ..."`` string literal, or a
    declaration keyword inside one, would be matched as real code. This scanner
    drops the string body entirely, so only genuine code reaches the keyword and
    declaration regexes. It also threads ``in_string`` so a string literal that
    spans several lines stays suppressed on every line it covers.

    Returns ``(code_only_text, new_nesting_depth, new_in_string)``.
    """
    out = []
    i = 0
    n = len(line)
    while i < n:
        ch = line[i]
        if in_string:
            # Inside a string literal: drop the content. A backslash escapes the
            # next char (so ``\"`` does not close the string); a bare ``"`` does.
            if ch == '\\' and i + 1 < n:
                i += 2
                continue
            if ch == '"':
                in_string = False
            i += 1
            continue
        if nesting_depth == 0:
            if ch == '"':
                in_string = True
                i += 1
                continue
            pair = line[i:i + 2]
            if pair == '/-':
                nesting_depth += 1
                i += 2
                continue
            if pair == '--':
                break  # rest of the line is a single-line comment
            out.append(ch)
            i += 1
            continue
        # Inside a block comment (nesting_depth > 0): consume until it closes.
        pair = line[i:i + 2]
        if pair == '/-':
            nesting_depth += 1
            i += 2
            continue
        if pair == '-/':
            nesting_depth -= 1
            i += 2
            continue
        i += 1
    return ''.join(out), nesting_depth, in_string


def detect_src_dir(root: str) -> Optional[str]:
    """Detect a Lean package's source directory by parsing its lakefile."""
    for lakefile, pattern in [
        ('lakefile.toml', r'srcDir\s*=\s*"([^"]+)"'),
        ('lakefile.lean', r'srcDir\s*:=\s*"([^"]+)"'),
    ]:
        path = os.path.join(root, lakefile)
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    m = re.search(pattern, f.read())
                if m:
                    return m.group(1)
            except Exception:
                pass
    return None


def import_search_dirs(repo_root: str) -> List[str]:
    """Directories under which a dotted Lean module may resolve to a file.

    Probing these directly avoids guessing each dependency's import root from
    its on-disk package directory name (the old ``str.capitalize()`` heuristic
    silently failed for packages like ``ProofWidgets``). For a module
    ``A.B.C`` the file ``A/B/C.lean`` is searched under, in order: the repo
    root, the repo's lakefile ``srcDir``, a conventional ``src/``, and every
    ``.lake/packages/<pkg>`` directory plus that package's own ``srcDir``.
    """
    dirs = [repo_root]
    src = detect_src_dir(repo_root)
    if src:
        dirs.append(os.path.join(repo_root, src))
    dirs.append(os.path.join(repo_root, 'src'))

    packages = os.path.join(repo_root, '.lake', 'packages')
    if os.path.isdir(packages):
        for pkg in sorted(os.listdir(packages)):
            pkg_dir = os.path.join(packages, pkg)
            if not os.path.isdir(pkg_dir):
                continue
            dirs.append(pkg_dir)
            psrc = detect_src_dir(pkg_dir)
            if psrc:
                dirs.append(os.path.join(pkg_dir, psrc))

    seen, out = set(), []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def resolve_import(import_path: str, search_dirs: List[str]) -> Optional[str]:
    """Resolve a dotted Lean module name to a file under one of search_dirs.

    Returns the first matching path, or None if the module can't be located.
    """
    parts = [p for p in import_path.split('.') if p]
    if not parts:
        return None
    rel = os.path.join(*parts) + '.lean'
    for base in search_dirs:
        candidate = os.path.join(base, rel)
        if os.path.isfile(candidate):
            return candidate
    return None
