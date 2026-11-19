"""Small KiCad S-expression reader used by the standalone preprocessor."""

from __future__ import annotations

import json


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    i = 0
    length = len(text)
    while i < length:
        char = text[i]
        if char.isspace():
            i += 1
            continue
        if char == ';':
            newline = text.find('\n', i)
            i = length if newline < 0 else newline + 1
            continue
        if char in '()':
            tokens.append(char)
            i += 1
            continue
        if char == '"':
            start = i
            i += 1
            escaped = False
            while i < length:
                current = text[i]
                if current == '"' and not escaped:
                    i += 1
                    break
                if current == '\\' and not escaped:
                    escaped = True
                else:
                    escaped = False
                i += 1
            else:
                raise ValueError("unterminated quoted string in KiCad S-expression")
            tokens.append(text[start:i])
            continue
        start = i
        while i < length and not text[i].isspace() and text[i] not in '();':
            i += 1
        tokens.append(text[start:i])
    return tokens


def _atom(token: str) -> str:
    if token.startswith('"'):
        try:
            return str(json.loads(token))
        except json.JSONDecodeError:
            return token[1:-1].replace('\\"', '"').replace('\\\\', '\\')
    return token


def parse_many(text: str) -> list[list]:
    stack: list[list] = []
    roots: list = []
    current = roots
    for token in tokenize(text):
        if token == '(':
            node: list = []
            current.append(node)
            stack.append(current)
            current = node
        elif token == ')':
            if not stack:
                raise ValueError("unexpected ')' in KiCad S-expression")
            current = stack.pop()
        else:
            current.append(_atom(token))
    if stack:
        raise ValueError("unclosed '(' in KiCad S-expression")
    if not all(isinstance(root, list) for root in roots):
        raise ValueError("expected KiCad S-expression roots")
    return roots


def parse(text: str) -> list:
    roots = parse_many(text)
    if len(roots) != 1 or not isinstance(roots[0], list):
        raise ValueError("expected one root KiCad S-expression")
    return roots[0]


def children(node: list, tag: str) -> list[list]:
    return [item for item in node if isinstance(item, list) and item and item[0] == tag]


def child(node: list, tag: str) -> list | None:
    return next(iter(children(node, tag)), None)


def value(node: list, tag: str, default: str = "") -> str:
    item = child(node, tag)
    return str(item[1]) if item is not None and len(item) > 1 else default


def number(raw: object, label: str) -> float:
    try:
        result = float(str(raw))
    except (TypeError, ValueError):
        raise ValueError(f"invalid numeric value for {label}: {raw!r}") from None
    return result
