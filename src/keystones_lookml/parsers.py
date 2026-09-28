"""A keystones parser plugin over lkml, so a view or a measure can be gated.

    parser = { plugin = "keystones_lookml.parsers:lookml" }

Every block is a definition, named by type and label under its parents:
`view.orders`, `view.orders.measure.total`, `explore.orders.join.users`.
The canonical rendering drops `description` and `label` by default, since
those churn without changing what a field computes, and reads a block's
parameters in sorted order, so reordering them is not a change.

Offsets come from lkml's own rendering, which round-trips the source byte
for byte. That is what puts a block's closing brace and every comment on the
line it was written on, without a second lexer.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import version

from keystones.parser import Definition, Unparseable

DEFAULT_DROP = ("description", "label")
# Bumped whenever the canonical rendering changes, so `migrate` can prove
# existing entries across instead of reading the change as drift.
RENDER_VERSION = 2

_COMMENT = re.compile(r"#[^\n]*")


def lookml(*, drop: list[str] | tuple[str, ...] = DEFAULT_DROP):
    """`drop` names parameters left out of the hash. An entry starting with `!`
    is an exception by block path, `!case.when.label`, kept in the hash."""
    if isinstance(drop, str) or not all(
        isinstance(d, str) and d.lstrip("!") for d in drop
    ):
        raise ValueError("drop must be a list of parameter names")
    return LookmlParser(tuple(sorted(set(drop))), version("lkml"))


@dataclass(frozen=True)
class LookmlParser:
    drop: tuple[str, ...]
    version: str

    @property
    def name(self) -> str:
        return "lookml"

    @property
    def identity(self) -> str:
        return f"lkml@{self.version}/render{RENDER_VERSION}"

    def parse(self, src: str):
        import lkml

        try:
            document = lkml.parse(src)
        except Exception as exc:  # lkml raises SyntaxError and its own errors
            raise Unparseable(
                str(exc).splitlines()[0] if str(exc) else "bad LookML"
            ) from exc
        return _Tree(src, document, self.drop)

    def parse_fragment(self, src: str):
        # A block slice is a document of its own; so is a run of parameters.
        return self.parse(src)


class _Tree:
    def __init__(self, src: str, document, drop: tuple[str, ...]):
        from lkml import tree

        self.src = src
        self.document = document
        self.drop = frozenset(d for d in drop if not d.startswith("!"))
        self.keep = tuple(tuple(d[1:].split(".")) for d in drop if d.startswith("!"))
        self._tree = tree
        self._defs: list[tuple[Definition, object]] = []
        self._comments: list[tuple[int, str]] = []
        end = self._document(document)
        if end != len(src):
            raise Unparseable("lkml did not round-trip this file, so lines are unknown")

    # --- offsets ------------------------------------------------------------

    def _line(self, offset: int) -> int:
        return self.src.count("\n", 0, offset) + 1

    def _trivia(self, text: str, offset: int) -> int:
        for match in _COMMENT.finditer(text):
            self._comments.append((self._line(offset + match.start()), match.group()))
        return offset + len(text)

    def _token(self, token, offset: int) -> int:
        if token is None:
            return offset
        if isinstance(token, str):
            return self._trivia(token, offset)
        offset = self._trivia(token.prefix, offset)
        offset += len(token.format_value())
        if isinstance(token, self._tree.ExpressionSyntaxToken):
            offset += len(token.expr_suffix) + 2
        return self._trivia(token.suffix, offset)

    def _document(self, document) -> int:
        offset = self._trivia(document.prefix, 0)
        offset = self._container(document.container, offset, "")
        return self._trivia(document.suffix, offset)

    def _container(self, container, offset: int, prefix: str) -> int:
        if container is None:
            return offset
        # Repeated unnamed siblings (`when: {}` twice under a `case`) would
        # share one qualname and the second would hash the first. They are
        # numbered by position; a block that appears once keeps its name.
        # Parameters repeat too: `include:` several times in a model file.
        counts: dict[tuple[str, str | None], int] = {}
        for item in container.items:
            counts[self._key(item)] = counts.get(self._key(item), 0) + 1
        seen: dict[tuple[str, str | None], int] = {}
        for item in container.items:
            index = None
            key = self._key(item)
            if counts[key] > 1 and key[1] is None:
                index = seen.get(key, 0)
                seen[key] = index + 1
            offset = self._node(item, offset, prefix, index)
        return offset

    def _key(self, item) -> tuple[str, str | None]:
        """What repeats: an unnamed block or a parameter by type. A named block
        repeating is an error LookML itself rejects, left for C6 to report."""
        tree = self._tree
        named = isinstance(item, tree.BlockNode) and item.name
        return (item.type.value, item.name.value if named else None)

    def _node(
        self, node, offset: int, prefix: str, index: int | None = None, in_list=False
    ) -> int:
        tree = self._tree
        if isinstance(node, tree.BlockNode):
            return self._block(node, offset, prefix, index)
        if isinstance(node, tree.PairNode):
            start = node.type.line_number
            offset = self._token(node.type, offset)
            offset = self._token(node.colon, offset)
            offset = self._token(node.value, offset)
            if not in_list:
                end = offset - len(node.value.suffix)
                self._parameter(node, prefix, start, end, index)
            return offset
        if isinstance(node, tree.ListNode):
            start = node.type.line_number
            offset = self._token(node.type, offset)
            offset = self._token(node.colon, offset)
            offset = self._token(node.left_bracket, offset)
            if node.leading_comma and node.items:
                offset = self._token(node.leading_comma, offset)
            for i, item in enumerate(node.items):
                if i:
                    offset += 1  # the comma lkml writes between items
                offset = self._node(item, offset, prefix, in_list=True)
            if node.trailing_comma and node.items:
                offset = self._token(node.trailing_comma, offset)
            offset = self._token(node.right_bracket, offset)
            end = offset - len(node.right_bracket.suffix)
            self._parameter(node, prefix, start, end, index)
            return offset
        return self._token(node, offset)

    def _parameter(
        self, node, prefix: str, start: int | None, end_offset: int, index=None
    ) -> None:
        """A parameter is addressable too, so a marker above `sql_always_where`
        covers that line and not the whole explore around it."""
        if start is None:
            return
        qualname = f"{prefix}.{node.type.value}" if prefix else node.type.value
        if index is not None:
            qualname += f"[{index}]"
        self._defs.append(
            (Definition(qualname, start, self._line(end_offset - 1)), node)
        )

    def _block(self, block, offset: int, prefix: str, index: int | None = None) -> int:
        start = block.type.line_number
        offset = self._token(block.type, offset)
        offset = self._token(block.colon, offset)
        offset = self._token(block.name, offset)
        offset = self._token(block.left_brace, offset)
        name = block.name.value if block.name else None
        qualname = ".".join(p for p in (prefix, block.type.value, name) if p)
        if index is not None:
            qualname += f"[{index}]"
        offset = self._container(block.container, offset, qualname)
        brace = offset + len(block.right_brace.prefix)
        self._defs.append((Definition(qualname, start, self._line(brace)), block))
        return self._token(block.right_brace, offset)

    # --- the contract -------------------------------------------------------

    def definitions(self) -> list[Definition]:
        return sorted((d for d, _ in self._defs), key=lambda d: (d.start, d.qualname))

    def comments(self) -> list[tuple[int, str]]:
        return sorted(self._comments)

    def render(self, definition: Definition | None) -> str:
        if definition is None:
            items = [self._canon(i) for i in self.document.container.items]
            kept = [i for i in items if i is not None]
            return json.dumps(sorted(kept, key=json.dumps), sort_keys=True)
        for found, block in self._defs:
            if found == definition:
                return json.dumps(self._canon(block), sort_keys=True)
        raise Unparseable(f"{definition.qualname} is not in this tree")

    def _dropped(self, name: str, path: tuple[str, ...]) -> bool:
        """Dropped by name, unless a `!block.path.name` exception keeps it.

        The path is relative to the block being rendered, so an exception is
        matched on the tail either way: `case.when.label` keeps the label under
        a keystone on the dimension and under one on the `when` block itself.
        """
        if name not in self.drop:
            return False
        full = (*path, name)
        for keep in self.keep:
            shorter = min(len(keep), len(full))
            if keep[-shorter:] == full[-shorter:]:
                return False
        return True

    def _canon(self, node, path: tuple[str, ...] = ()) -> object:
        tree = self._tree
        if isinstance(node, tree.BlockNode):
            inner = (*path, node.type.value)
            items = node.container.items if node.container else ()
            children = [self._canon(i, inner) for i in items]
            return {
                "b": node.type.value,
                "n": node.name.value if node.name else None,
                "c": sorted((c for c in children if c is not None), key=json.dumps),
            }
        if isinstance(node, tree.PairNode):
            if self._dropped(node.type.value, path):
                return None
            return ["p", node.type.value, *self._value(node.value)]
        if isinstance(node, tree.ListNode):
            if self._dropped(node.type.value, path):
                return None
            items = [self._canon(i, path) for i in node.items]
            return ["l", node.type.value, [i for i in items if i is not None]]
        return list(self._value(node))

    def _value(self, token) -> tuple[str, str]:
        tree = self._tree
        if isinstance(token, tree.ExpressionSyntaxToken):
            return "e", " ".join(token.value.split())
        if isinstance(token, tree.QuotedSyntaxToken):
            return "q", token.value
        return "", token.value
