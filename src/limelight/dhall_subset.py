"""Evaluate the subset of Dhall that Limelight manifests use, straight to JSON.

A ``project.dhall`` and the language reference it imports are plain data:
``let`` bindings, relative imports, records, unions, ``Some``/``None``, lists,
literals, field access and ``//``. None of the parts of Dhall that make it a
programming language - functions, ``if``, ``merge``, text interpolation, remote
imports - ever appear, because the writer never emits them and the reference
never needed them. That is a small enough language to evaluate here, which
spares every install from carrying a 35 MB ``dhall-to-json`` binary.

The contract is "produces exactly what ``dhall-to-json`` would", including its
JSON conventions, which the reader depends on:

* ``Some x`` becomes ``x`` and ``None`` becomes ``null``;
* a union alternative with no payload becomes its name as a string, and one
  with a payload becomes the payload alone - the name is dropped;
* record fields whose value is ``null`` are omitted (``dhall-to-json`` does
  this unless told ``--preserve-null``), while ``null`` list elements stay;
* a ``Double`` with no fractional part is written as an integer.

``tests/test_dhall_subset.py`` checks that contract against the real tool.

Anything outside the subset raises :class:`UnsupportedDhall`, which names the
construct so the caller can fall back to ``dhall-to-json`` or tell the user.
Association lists (``List { mapKey : Text, mapValue : T }``) are the one
``dhall-to-json`` convention not reproduced; Limelight never uses them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any


class DhallError(Exception):
    """A manifest could not be evaluated. ``str(error)`` names the file and place."""


class DhallSyntaxError(DhallError):
    """The text is not the Dhall this module understands - possibly not Dhall at all."""


class UnsupportedDhall(DhallError):
    """Valid Dhall, but outside the subset this module evaluates."""


class DhallEvaluationError(DhallError):
    """The expression parsed but does not evaluate: an unbound name, a missing field, a bad import."""


# ---------------------------------------------------------------------------
# Tokens


@dataclass(frozen=True)
class Token:
    kind: str  # label, keyword, natural, integer, double, text, import, punct, eof
    text: str
    line: int
    column: int


_KEYWORDS = {
    "let", "in", "Some", "if", "then", "else", "merge", "toMap", "assert",
    "forall", "with", "missing", "as", "using",
}

# Operators and punctuation, longest first so ``==`` beats ``=`` and so on.
_SLASH_OPERATORS = ["//\\\\", "//", "/\\"]
_PUNCTUATION = [
    "===", "->", "→", "++", "&&", "||", "==", "!=", "::",
    "λ", "∀", "\\", "#", "+", "*", "?", ".", ",", ":", "=", "|", "<", ">",
    "{", "}", "[", "]", "(", ")", "@",
]

_LABEL = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:/[A-Za-z][A-Za-z0-9_-]*)*")
_QUOTED_LABEL = re.compile(r"`[^`]*`")
_DOUBLE = re.compile(r"[+-]?\d+(?:\.\d+(?:[eE][+-]?\d+)?|[eE][+-]?\d+)")
_INTEGER = re.compile(r"[+-]\d+")
_NATURAL = re.compile(r"\d+")
# A local import: ``./``, ``../``, ``~/`` or ``/`` then path characters, of
# which ``/`` separates components. The excluded characters are the ones the
# Dhall grammar keeps out of a path component.
_IMPORT = re.compile(r"(?:\.\./|\./|~/|/)[^\s$(),<>?\[\\\]{}\"]*")
_REMOTE_IMPORT = re.compile(r"(?:https?://|env:)")
_SIMPLE_ESCAPES = {
    '"': '"', "$": "$", "\\": "\\", "/": "/",
    "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t",
}


class _Tokenizer:
    def __init__(self, source: str, filename: str) -> None:
        self.source = source
        self.filename = filename
        self.position = 0
        self.line = 1
        self.column = 1

    def error(self, message: str, kind: type[DhallError] = DhallSyntaxError) -> DhallError:
        return kind(f"{self.filename}:{self.line}:{self.column}: {message}")

    def advance(self, count: int) -> None:
        chunk = self.source[self.position : self.position + count]
        newlines = chunk.count("\n")
        if newlines:
            self.line += newlines
            self.column = count - chunk.rfind("\n")
        else:
            self.column += count
        self.position += count

    def tokens(self) -> list[Token]:
        result: list[Token] = []
        while True:
            self.skip_whitespace_and_comments()
            if self.position >= len(self.source):
                result.append(Token("eof", "", self.line, self.column))
                return result
            result.append(self.next_token())

    def skip_whitespace_and_comments(self) -> None:
        source = self.source
        while self.position < len(source):
            character = source[self.position]
            if character in " \t\r\n":
                self.advance(1)
            elif source.startswith("--", self.position):
                end = source.find("\n", self.position)
                self.advance((len(source) if end < 0 else end) - self.position)
            elif source.startswith("{-", self.position):
                self.skip_block_comment()
            else:
                return

    def skip_block_comment(self) -> None:
        # Block comments nest, so count openers rather than searching for the
        # first closer.
        depth = 0
        source = self.source
        start_line, start_column = self.line, self.column
        while self.position < len(source):
            if source.startswith("{-", self.position):
                depth += 1
                self.advance(2)
            elif source.startswith("-}", self.position):
                depth -= 1
                self.advance(2)
                if depth == 0:
                    return
            else:
                self.advance(1)
        raise DhallSyntaxError(f"{self.filename}:{start_line}:{start_column}: unterminated block comment")

    def next_token(self) -> Token:
        source, position = self.source, self.position
        line, column = self.line, self.column

        def token(kind: str, text: str) -> Token:
            self.advance(len(text))
            return Token(kind, text, line, column)

        character = source[position]
        if character == '"':
            return self.text_literal()
        if source.startswith("''", position):
            raise self.error("multi-line text literals ('' ... '') are not supported", UnsupportedDhall)
        if _REMOTE_IMPORT.match(source, position):
            raise self.error("remote and environment imports are not supported", UnsupportedDhall)

        for pattern, kind in ((_DOUBLE, "double"), (_INTEGER, "integer"), (_NATURAL, "natural")):
            match = pattern.match(source, position)
            if match:
                return token(kind, match.group())

        match = _QUOTED_LABEL.match(source, position)
        if match:
            self.advance(len(match.group()))
            return Token("label", match.group()[1:-1], line, column)

        match = _LABEL.match(source, position)
        if match:
            text = match.group()
            return token("keyword" if text in _KEYWORDS else "label", text)

        # The slash operators come before imports so ``//`` is not read as a
        # path; everything else comes after so ``./x`` is not read as ``.``.
        for punctuation in _SLASH_OPERATORS:
            if source.startswith(punctuation, position):
                return token("punct", punctuation)
        match = _IMPORT.match(source, position)
        if match:
            return token("import", match.group())
        for punctuation in _PUNCTUATION:
            if source.startswith(punctuation, position):
                return token("punct", punctuation)

        raise self.error(f"unexpected character {character!r}")

    def text_literal(self) -> Token:
        source = self.source
        line, column = self.line, self.column
        end = self.position + 1
        pieces: list[str] = []
        while True:
            if end >= len(source):
                raise DhallSyntaxError(f"{self.filename}:{line}:{column}: unterminated text literal")
            character = source[end]
            if character == '"':
                end += 1
                break
            if source.startswith("${", end):
                raise UnsupportedDhall(
                    f"{self.filename}:{line}:{column}: text interpolation (${{...}}) is not supported"
                )
            if character != "\\":
                pieces.append(character)
                end += 1
                continue
            escape = source[end + 1 : end + 2]
            if escape in _SIMPLE_ESCAPES:
                pieces.append(_SIMPLE_ESCAPES[escape])
                end += 2
            elif escape == "u":
                code_point, end = self.unicode_escape(end + 2, line, column)
                pieces.append(chr(code_point))
            else:
                raise DhallSyntaxError(f"{self.filename}:{line}:{column}: invalid escape \\{escape}")
        self.advance(end - self.position)
        return Token("text", "".join(pieces), line, column)

    def unicode_escape(self, start: int, line: int, column: int) -> tuple[int, int]:
        source = self.source
        if source.startswith("{", start):
            close = source.find("}", start)
            digits = source[start + 1 : close] if close > 0 else ""
            end = close + 1
        else:
            digits = source[start : start + 4]
            end = start + 4
        if not digits or not re.fullmatch(r"[0-9A-Fa-f]{1,6}", digits):
            raise DhallSyntaxError(f"{self.filename}:{line}:{column}: invalid unicode escape")
        code_point = int(digits, 16)
        # Dhall, unlike JSON, has no surrogate pairs: astral characters are
        # written with the braced form, and a lone surrogate is an error.
        if 0xD800 <= code_point <= 0xDFFF or code_point > 0x10FFFF:
            raise DhallSyntaxError(
                f"{self.filename}:{line}:{column}: \\u{digits} is not a valid Dhall code point "
                "(use \\u{...} for characters outside the basic plane)"
            )
        return code_point, end


# ---------------------------------------------------------------------------
# Syntax tree


@dataclass(frozen=True)
class Node:
    line: int
    column: int


@dataclass(frozen=True)
class Literal(Node):
    value: Any


@dataclass(frozen=True)
class Variable(Node):
    name: str


@dataclass(frozen=True)
class Import(Node):
    path: str


@dataclass(frozen=True)
class Let(Node):
    name: str
    annotation: Node | None
    value: Node
    body: Node


@dataclass(frozen=True)
class Annotated(Node):
    expression: Node
    annotation: Node


@dataclass(frozen=True)
class Prefer(Node):  # left // right
    left: Node
    right: Node


@dataclass(frozen=True)
class SomeExpression(Node):
    argument: Node


@dataclass(frozen=True)
class Application(Node):
    function: Node
    arguments: tuple[Node, ...]


@dataclass(frozen=True)
class Field(Node):
    record: Node
    name: str


@dataclass(frozen=True)
class RecordLiteral(Node):
    fields: tuple[tuple[str, Node], ...]


@dataclass(frozen=True)
class RecordType(Node):
    fields: tuple[tuple[str, Node], ...]


@dataclass(frozen=True)
class UnionType(Node):
    alternatives: tuple[tuple[str, Node | None], ...]


@dataclass(frozen=True)
class ListLiteral(Node):
    items: tuple[Node, ...]


# ---------------------------------------------------------------------------
# Parser

_PRIMITIVE_STARTS = {"label", "natural", "integer", "double", "text", "import"}
_PRIMITIVE_PUNCTUATION = {"{", "[", "<", "("}
_UNSUPPORTED_KEYWORDS = {
    "if": "if/then/else",
    "merge": "merge",
    "toMap": "toMap",
    "assert": "assert",
    "forall": "function types (forall)",
    "with": "with-expressions",
    "missing": "the missing import",
}
_UNSUPPORTED_OPERATORS = {
    "\\": "lambdas", "λ": "lambdas", "∀": "function types", "->": "function types", "→": "function types",
    "++": "the ++ operator", "#": "the # operator", "&&": "the && operator", "||": "the || operator",
    "+": "the + operator", "*": "the * operator", "==": "the == operator", "!=": "the != operator",
    "===": "assert", "/\\": "the /\\ operator", "//\\\\": "the //\\\\ operator", "?": "import fallbacks (?)",
    "::": "record completion (::)", "@": "indexed variables (x@n)",
}


class _Parser:
    def __init__(self, tokens: list[Token], filename: str) -> None:
        self.tokens = tokens
        self.index = 0
        self.filename = filename

    @property
    def current(self) -> Token:
        return self.tokens[self.index]

    def error(self, message: str, token: Token | None = None, kind: type[DhallError] = DhallSyntaxError) -> DhallError:
        token = token or self.current
        return kind(f"{self.filename}:{token.line}:{token.column}: {message}")

    def unsupported(self, construct: str, token: Token | None = None) -> DhallError:
        return self.error(f"{construct} not supported by the built-in Dhall evaluator", token, UnsupportedDhall)

    def at(self, kind: str, text: str | None = None) -> bool:
        token = self.current
        return token.kind == kind and (text is None or token.text == text)

    def take(self) -> Token:
        token = self.current
        self.index += 1
        return token

    def expect(self, kind: str, text: str | None = None) -> Token:
        if not self.at(kind, text):
            wanted = repr(text) if text else kind
            found = repr(self.current.text) if self.current.kind != "eof" else "end of file"
            raise self.error(f"expected {wanted}, found {found}")
        return self.take()

    def parse_file(self) -> Node:
        expression = self.parse_expression()
        if not self.at("eof"):
            raise self.error(f"unexpected {self.current.text!r} after the expression")
        return expression

    def parse_expression(self) -> Node:
        token = self.current
        if self.at("keyword", "let"):
            return self.parse_let()
        if token.kind == "keyword" and token.text in _UNSUPPORTED_KEYWORDS:
            raise self.unsupported(_UNSUPPORTED_KEYWORDS[token.text])
        if token.kind == "punct" and token.text in {"\\", "λ", "∀"}:
            raise self.unsupported(_UNSUPPORTED_OPERATORS[token.text])
        return self.parse_annotated()

    def parse_let(self) -> Node:
        bindings: list[tuple[Token, str, Node | None, Node]] = []
        while self.at("keyword", "let"):
            start = self.take()
            name = self.expect("label").text
            annotation = None
            if self.at("punct", ":"):
                self.take()
                annotation = self.parse_expression()
            self.expect("punct", "=")
            value = self.parse_expression()
            bindings.append((start, name, annotation, value))
        self.expect("keyword", "in")
        body = self.parse_expression()
        for start, name, annotation, value in reversed(bindings):
            body = Let(start.line, start.column, name, annotation, value, body)
        return body

    def parse_annotated(self) -> Node:
        expression = self.parse_operators()
        if self.at("punct", ":"):
            colon = self.take()
            annotation = self.parse_expression()
            return Annotated(colon.line, colon.column, expression, annotation)
        return expression

    def parse_operators(self) -> Node:
        left = self.parse_application()
        while True:
            operator = self.current
            if operator.kind == "keyword" and operator.text == "with":
                raise self.unsupported(_UNSUPPORTED_KEYWORDS["with"])
            if operator.kind != "punct":
                break
            if operator.text == "//":
                self.take()
                right = self.parse_application()
                left = Prefer(operator.line, operator.column, left, right)
            elif operator.text in _UNSUPPORTED_OPERATORS:
                raise self.unsupported(_UNSUPPORTED_OPERATORS[operator.text])
            else:
                break
        return left

    def parse_application(self) -> Node:
        if self.at("keyword", "Some"):
            some = self.take()
            head: Node = SomeExpression(some.line, some.column, self.parse_selector())
        else:
            head = self.parse_selector()
        arguments: list[Node] = []
        while self.starts_primitive():
            arguments.append(self.parse_selector())
        if not arguments:
            return head
        return Application(head.line, head.column, head, tuple(arguments))

    def starts_primitive(self) -> bool:
        token = self.current
        if token.kind in _PRIMITIVE_STARTS:
            return True
        return token.kind == "punct" and token.text in _PRIMITIVE_PUNCTUATION

    def parse_selector(self) -> Node:
        expression = self.parse_primitive()
        while self.at("punct", "."):
            dot = self.take()
            if self.at("label"):
                expression = Field(dot.line, dot.column, expression, self.take().text)
            elif self.at("punct", "{"):
                raise self.unsupported("record projection (r.{a, b})", dot)
            elif self.at("punct", "("):
                raise self.unsupported("projection by type (r.(T))", dot)
            else:
                raise self.error("expected a field name after '.'")
        return expression

    def parse_primitive(self) -> Node:
        token = self.current
        line, column = token.line, token.column
        if token.kind == "natural":
            self.take()
            return Literal(line, column, int(token.text))
        if token.kind == "integer":
            self.take()
            return Literal(line, column, int(token.text))
        if token.kind == "double":
            self.take()
            return Literal(line, column, float(token.text))
        if token.kind == "text":
            self.take()
            return Literal(line, column, token.text)
        if token.kind == "label":
            self.take()
            return Variable(line, column, token.text)
        if token.kind == "import":
            self.take()
            if self.at("keyword", "as") or self.at("label", "sha256") or self.at("keyword", "using"):
                raise self.unsupported("import qualifiers (as, sha256:, using)")
            return Import(line, column, token.text)
        if token.kind == "punct":
            if token.text == "(":
                self.take()
                inner = self.parse_expression()
                self.expect("punct", ")")
                return inner
            if token.text == "{":
                return self.parse_record()
            if token.text == "[":
                return self.parse_list()
            if token.text == "<":
                return self.parse_union()
            if token.text in _UNSUPPORTED_OPERATORS:
                raise self.unsupported(_UNSUPPORTED_OPERATORS[token.text])
        if token.kind == "keyword" and token.text in _UNSUPPORTED_KEYWORDS:
            raise self.unsupported(_UNSUPPORTED_KEYWORDS[token.text])
        found = "end of file" if token.kind == "eof" else repr(token.text)
        raise self.error(f"expected an expression, found {found}")

    def skip_leading_separator(self, separator: str) -> None:
        # Dhall allows a leading ``,`` or ``|`` so lists line up when written
        # one element per line.
        if self.at("punct", separator):
            self.take()

    def parse_record(self) -> Node:
        opener = self.expect("punct", "{")
        line, column = opener.line, opener.column
        if self.at("punct", "}"):
            self.take()
            return RecordType(line, column, ())
        if self.at("punct", "="):
            self.take()
            self.expect("punct", "}")
            return RecordLiteral(line, column, ())
        self.skip_leading_separator(",")

        fields: list[tuple[str, Node]] = []
        is_type: bool | None = None
        while True:
            name_token = self.expect("label")
            if self.at("punct", "."):
                raise self.unsupported("dotted record fields ({ a.b = x })", name_token)
            if self.at("punct", ":"):
                separator = ":"
            elif self.at("punct", "="):
                separator = "="
            elif self.at("punct", ",") or self.at("punct", "}"):
                raise self.unsupported("record punning ({ a, b })", name_token)
            else:
                raise self.error("expected ':' or '=' after the field name")
            if is_type is None:
                is_type = separator == ":"
            elif is_type != (separator == ":"):
                raise self.error("cannot mix record type fields (a : T) and record literal fields (a = x)")
            self.take()
            fields.append((name_token.text, self.parse_expression()))
            if self.at("punct", ","):
                self.take()
                continue
            self.expect("punct", "}")
            break
        node_type = RecordType if is_type else RecordLiteral
        return node_type(line, column, tuple(fields))

    def parse_list(self) -> Node:
        opener = self.expect("punct", "[")
        items: list[Node] = []
        self.skip_leading_separator(",")
        while not self.at("punct", "]"):
            items.append(self.parse_expression())
            if self.at("punct", ","):
                self.take()
            elif not self.at("punct", "]"):
                raise self.error("expected ',' or ']' in list")
        self.take()
        return ListLiteral(opener.line, opener.column, tuple(items))

    def parse_union(self) -> Node:
        opener = self.expect("punct", "<")
        alternatives: list[tuple[str, Node | None]] = []
        self.skip_leading_separator("|")
        while not self.at("punct", ">"):
            name = self.expect("label").text
            payload = None
            if self.at("punct", ":"):
                self.take()
                payload = self.parse_expression()
            alternatives.append((name, payload))
            if self.at("punct", "|"):
                self.take()
            elif not self.at("punct", ">"):
                raise self.error("expected '|' or '>' in union type")
        self.take()
        return UnionType(opener.line, opener.column, tuple(alternatives))


def parse(source: str, filename: str = "<dhall>") -> Node:
    return _Parser(_Tokenizer(source, filename).tokens(), filename).parse_file()


# ---------------------------------------------------------------------------
# Values


@dataclass(frozen=True)
class TypeValue:
    """A type used as a value: ``Text``, ``List Natural``, ``{ a : Text }``.

    Types only ever appear in annotations and the language reference's export
    records, so nothing is checked - they just need to evaluate to something.
    """

    description: str


@dataclass(frozen=True)
class UnionTypeValue:
    alternatives: dict[str, Any]  # name -> payload type, or None for a bare alternative


@dataclass(frozen=True)
class Constructor:
    """A union alternative with a payload, waiting to be applied to it."""

    name: str


@dataclass(frozen=True)
class UnionValue:
    name: str
    payload: Any  # None for a bare alternative


@dataclass(frozen=True)
class SomeValue:
    value: Any


class _NoneValue:
    def __repr__(self) -> str:
        return "None"


NONE = _NoneValue()


class RecordValue(dict):
    """A record literal. A dict subclass so ``//`` and field access stay trivial."""


class ListValue(list):
    """A list literal, kept distinct from the tuples used inside the syntax tree."""


_BUILTIN_TYPES = ("Bool", "Natural", "Integer", "Double", "Text", "List", "Optional", "Type", "Kind", "Sort", "Date", "Time", "TimeZone", "Bytes")
_BUILTINS: dict[str, Any] = {"True": True, "False": False, "None": Constructor("None")}
_BUILTINS.update({name: TypeValue(name) for name in _BUILTIN_TYPES})
_OPTIONAL_TYPE = _BUILTINS["Optional"]
_LIST_TYPE = _BUILTINS["List"]


# ---------------------------------------------------------------------------
# Evaluation


class _Evaluator:
    def __init__(self) -> None:
        self.imports: dict[Path, Any] = {}
        self.importing: list[Path] = []

    def evaluate_file(self, path: Path) -> Any:
        path = path.resolve()
        if path in self.imports:
            return self.imports[path]
        if path in self.importing:
            cycle = " -> ".join(str(item) for item in self.importing[self.importing.index(path) :])
            raise DhallEvaluationError(f"import cycle: {cycle} -> {path}")
        try:
            source = path.read_text(encoding="utf-8")
        except OSError as error:
            raise DhallEvaluationError(f"cannot read import {path}: {error.strerror}") from error
        self.importing.append(path)
        try:
            # An imported file is a closed expression: it cannot see the
            # importer's bindings, only its own and other imports.
            value = self.evaluate(parse(source, str(path)), {}, path.parent, str(path))
        finally:
            self.importing.pop()
        self.imports[path] = value
        return value

    def evaluate(self, node: Node, env: dict[str, Any], base: Path, filename: str) -> Any:
        def error(message: str, kind: type[DhallError] = DhallEvaluationError) -> DhallError:
            return kind(f"{filename}:{node.line}:{node.column}: {message}")

        if isinstance(node, Literal):
            return node.value

        if isinstance(node, Variable):
            if node.name in env:
                return env[node.name]
            if node.name in _BUILTINS:
                return _BUILTINS[node.name]
            if "/" in node.name:
                raise error(f"built-in function {node.name} is not supported by the built-in Dhall evaluator", UnsupportedDhall)
            raise error(f"unbound variable {node.name}")

        if isinstance(node, Import):
            if node.path.startswith("~/"):
                raise error("home-relative imports (~/) are not supported", UnsupportedDhall)
            target = Path(node.path) if node.path.startswith("/") else base / node.path
            return self.evaluate_file(target)

        if isinstance(node, Let):
            if node.annotation is not None:
                self.evaluate(node.annotation, env, base, filename)
            value = self.evaluate(node.value, env, base, filename)
            return self.evaluate(node.body, {**env, node.name: value}, base, filename)

        if isinstance(node, Annotated):
            # The annotation is not checked against the value, but evaluating
            # it does catch a misspelt type name, as Dhall would.
            self.evaluate(node.annotation, env, base, filename)
            return self.evaluate(node.expression, env, base, filename)

        if isinstance(node, Prefer):
            left = self.evaluate(node.left, env, base, filename)
            right = self.evaluate(node.right, env, base, filename)
            if not isinstance(left, RecordValue) or not isinstance(right, RecordValue):
                raise error("// needs a record on both sides")
            return RecordValue({**left, **right})

        if isinstance(node, SomeExpression):
            return SomeValue(self.evaluate(node.argument, env, base, filename))

        if isinstance(node, Application):
            value = self.evaluate(node.function, env, base, filename)
            for argument_node in node.arguments:
                argument = self.evaluate(argument_node, env, base, filename)
                value = self.apply(value, argument, error)
            return value

        if isinstance(node, Field):
            record = self.evaluate(node.record, env, base, filename)
            if isinstance(record, RecordValue):
                if node.name not in record:
                    raise error(f"record has no field {node.name!r} (fields: {', '.join(record) or 'none'})")
                return record[node.name]
            if isinstance(record, UnionTypeValue):
                if node.name not in record.alternatives:
                    raise error(f"union has no alternative {node.name!r}")
                if record.alternatives[node.name] is None:
                    return UnionValue(node.name, None)
                return Constructor(node.name)
            raise error(f"cannot select field {node.name!r} from {describe(record)}")

        if isinstance(node, RecordLiteral):
            fields = RecordValue()
            for name, value_node in node.fields:
                if name in fields:
                    raise error(f"duplicate record field {name!r}")
                fields[name] = self.evaluate(value_node, env, base, filename)
            return fields

        if isinstance(node, RecordType):
            for _, type_node in node.fields:
                self.evaluate(type_node, env, base, filename)
            return TypeValue("{ " + ", ".join(f"{name} : ..." for name, _ in node.fields) + " }")

        if isinstance(node, UnionType):
            alternatives: dict[str, Any] = {}
            for name, type_node in node.alternatives:
                if name in alternatives:
                    raise error(f"duplicate union alternative {name!r}")
                alternatives[name] = None if type_node is None else self.evaluate(type_node, env, base, filename)
            return UnionTypeValue(alternatives)

        if isinstance(node, ListLiteral):
            return ListValue(self.evaluate(item, env, base, filename) for item in node.items)

        raise error(f"internal error: no rule for {type(node).__name__}")

    @staticmethod
    def apply(function: Any, argument: Any, error: Any) -> Any:
        if isinstance(function, Constructor):
            if function.name == "None":
                return NONE
            return UnionValue(function.name, argument)
        if function is _LIST_TYPE or function is _OPTIONAL_TYPE:
            return TypeValue(f"{function.description} {describe(argument)}")
        if isinstance(function, TypeValue):
            raise error(f"{function.description} cannot be applied to an argument")
        if isinstance(function, UnionValue):
            raise error(f"union alternative {function.name} takes no payload")
        # Lambdas are rejected at parse time, so nothing else is callable.
        raise error(f"cannot apply {describe(function)} to an argument")


def describe(value: Any) -> str:
    if isinstance(value, TypeValue):
        return value.description
    if isinstance(value, UnionTypeValue):
        return "a union type"
    if isinstance(value, RecordValue):
        return "a record"
    if isinstance(value, ListValue):
        return "a list"
    if isinstance(value, Constructor):
        return f"the constructor {value.name}"
    if isinstance(value, UnionValue):
        return f"the union value {value.name}"
    if isinstance(value, SomeValue):
        return "an Optional"
    if value is NONE:
        return "None"
    return f"a {type(value).__name__}"


# ---------------------------------------------------------------------------
# JSON


def _double_to_json(value: float) -> int | float:
    # ``dhall-to-json`` writes a whole-number Double without a fractional part,
    # so ``210.0`` reads back as the integer 210. Going through the shortest
    # decimal representation, rather than ``int(value)``, matches how it
    # expands large values such as ``1.5e300``.
    if value != value or value in (float("inf"), float("-inf")):
        raise DhallEvaluationError("NaN and Infinity cannot be converted to JSON")
    if value == int(value):
        return int(Decimal(repr(value)))
    return value


def to_json(value: Any) -> Any:
    """Convert an evaluated value the way ``dhall-to-json`` would."""

    if isinstance(value, bool) or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return _double_to_json(value)
    if isinstance(value, RecordValue):
        # Fields that come out null are dropped: dhall-to-json's default.
        converted = ((name, to_json(item)) for name, item in value.items())
        return {name: item for name, item in converted if item is not None}
    if isinstance(value, ListValue):
        return [to_json(item) for item in value]
    if isinstance(value, SomeValue):
        return to_json(value.value)
    if value is NONE:
        return None
    if isinstance(value, UnionValue):
        return value.name if value.payload is None else to_json(value.payload)
    raise DhallEvaluationError(f"cannot convert {describe(value)} to JSON")


def load(path: str | Path) -> Any:
    """Evaluate a Dhall file and return its value as JSON-shaped Python data."""

    return to_json(_Evaluator().evaluate_file(Path(path)))


def loads(source: str, *, base: str | Path | None = None, filename: str = "<dhall>") -> Any:
    """Evaluate Dhall source text; relative imports resolve against ``base``."""

    base_path = Path(base) if base is not None else Path.cwd()
    return to_json(_Evaluator().evaluate(parse(source, filename), {}, base_path, filename))
