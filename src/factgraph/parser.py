from __future__ import annotations

from dataclasses import dataclass
import ast as py_ast
import re
from typing import Any

from .ast import (
    AnalysisDecl,
    EntityDecl,
    FactDecl,
    FieldDecl,
    FrequencyDecl,
    MandatoryDecl,
    ModelAst,
    RingDecl,
    RoleDecl,
    RoleSequenceDecl,
    SampleDecl,
    SetConstraintDecl,
    SubtypeDecl,
    UniqueDecl,
    UnorderedDecl,
    ValueConstraintDecl,
    ValueDecl,
)


class ParseError(ValueError):
    pass


@dataclass(frozen=True)
class Token:
    kind: str
    value: str
    line: int
    col: int


_TOKEN_RE = re.compile(
    r"(?P<WS>[ \t\r\n]+)"
    r"|(?P<COMMENT>\#[^\n]*|//[^\n]*)"
    r"|(?P<STRING>\"(?:\\.|[^\"\\])*\")"
    r"|(?P<NUMBER>-?(?:[0-9]+(?:\.[0-9]+)?))"
    r"|(?P<IDENT>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<PUNCT>[{}():,?])"
)


def _tokenize(text: str) -> list[Token]:
    tokens: list[Token] = []
    pos = 0
    line = 1
    col = 1
    while pos < len(text):
        m = _TOKEN_RE.match(text, pos)
        if not m:
            excerpt = text[pos : pos + 30].split("\n", 1)[0]
            raise ParseError(f"line {line}, col {col}: unexpected input {excerpt!r}")
        raw = m.group(0)
        kind = m.lastgroup or ""
        if kind not in {"WS", "COMMENT"}:
            value = raw
            if kind == "PUNCT":
                kind = raw
            tokens.append(Token(kind, value, line, col))
        newlines = raw.count("\n")
        if newlines:
            line += newlines
            col = len(raw.rsplit("\n", 1)[-1]) + 1
        else:
            col += len(raw)
        pos = m.end()
    tokens.append(Token("EOF", "", line, col))
    return tokens


class Parser:
    def __init__(self, text: str):
        self.tokens = _tokenize(text)
        self.i = 0

    def peek(self, value: str | None = None, kind: str | None = None) -> bool:
        t = self.tokens[self.i]
        if value is not None and t.value != value:
            return False
        if kind is not None and t.kind != kind:
            return False
        return True

    def pop(self) -> Token:
        t = self.tokens[self.i]
        self.i += 1
        return t

    def expect(self, value: str | None = None, kind: str | None = None) -> Token:
        t = self.tokens[self.i]
        if value is not None and t.value != value:
            raise ParseError(f"line {t.line}, col {t.col}: expected {value!r}, got {t.value!r}")
        if kind is not None and t.kind != kind:
            raise ParseError(f"line {t.line}, col {t.col}: expected {kind}, got {t.kind} {t.value!r}")
        self.i += 1
        return t

    def ident(self) -> Token:
        return self.expect(kind="IDENT")

    def scalar_literal(self) -> Any:
        t = self.tokens[self.i]
        if t.kind == "STRING":
            self.pop()
            try:
                return py_ast.literal_eval(t.value)
            except Exception as exc:
                raise ParseError(f"line {t.line}: invalid string literal") from exc
        if t.kind == "NUMBER":
            self.pop()
            return float(t.value) if "." in t.value else int(t.value)
        if t.kind == "IDENT":
            self.pop()
            if t.value == "true":
                return True
            if t.value == "false":
                return False
            return t.value
        raise ParseError(f"line {t.line}, col {t.col}: expected scalar literal")

    def optional_identity(self) -> str | None:
        if not self.peek("identity"):
            return None
        start = self.pop()
        token = self.expect(kind="STRING")
        try:
            value = py_ast.literal_eval(token.value)
        except Exception as exc:
            raise ParseError(f"line {token.line}: invalid identity string") from exc
        if not isinstance(value, str) or not value.strip():
            raise ParseError(f"line {start.line}: identity must be a non-empty string")
        return value

    def parse(self) -> ModelAst:
        self.expect("model")
        name = self.ident().value
        identity = self.optional_identity()
        self.expect("{")
        model = ModelAst(name, identity)
        while not self.peek("}"):
            if self.peek("value"):
                model.values.append(self.parse_value())
            elif self.peek("entity"):
                model.entities.append(self.parse_entity())
            elif self.peek("fact"):
                model.facts.append(self.parse_fact())
            elif self.peek("subtype"):
                model.subtypes.append(self.parse_subtype())
            elif self.peek("subset") or self.peek("equality") or self.peek("exclusion"):
                model.set_constraints.append(self.parse_set_constraint())
            elif self.peek("sample"):
                model.samples.append(self.parse_sample())
            elif self.peek("analysis"):
                model.analyses.append(self.parse_analysis())
            else:
                t = self.tokens[self.i]
                raise ParseError(
                    f"line {t.line}, col {t.col}: expected value/entity/fact/subtype/subset/equality/exclusion/sample/analysis, got {t.value!r}"
                )
        self.expect("}")
        self.expect(kind="EOF")
        return model

    def parse_value(self) -> ValueDecl:
        start = self.expect("value")
        name = self.ident().value
        self.expect(":")
        scalar = self.ident().value
        identity = self.optional_identity()
        constraints: list[ValueConstraintDecl] = []
        if self.peek("{"):
            self.pop()
            while not self.peek("}"):
                cstart = self.tokens[self.i]
                if self.peek("range"):
                    self.pop()
                    self.expect("(")
                    lo = self.scalar_literal()
                    self.expect(",")
                    hi = self.scalar_literal()
                    self.expect(")")
                    constraints.append(ValueConstraintDecl("range", (lo, hi), cstart.line))
                elif self.peek("oneof"):
                    self.pop()
                    self.expect("(")
                    vals: list[Any] = []
                    if not self.peek(")"):
                        while True:
                            vals.append(self.scalar_literal())
                            if self.peek(","):
                                self.pop()
                                continue
                            break
                    self.expect(")")
                    constraints.append(ValueConstraintDecl("oneof", tuple(vals), cstart.line))
                else:
                    raise ParseError(f"line {cstart.line}: expected range(...) or oneof(...) in value constraint block")
            self.expect("}")
        return ValueDecl(name, scalar, tuple(constraints), identity, start.line)

    def parse_entity(self) -> EntityDecl:
        start = self.expect("entity")
        name = self.ident().value
        identity = self.optional_identity()
        self.expect("{")
        fields: list[FieldDecl] = []
        while not self.peek("}"):
            identifier = False
            if self.peek("id"):
                self.pop()
                identifier = True
            fstart = self.tokens[self.i]
            fname = self.ident().value
            self.expect(":")
            ftype = self.ident().value
            required = True
            if self.peek("?"):
                self.pop()
                required = False
            field_identity = self.optional_identity()
            fields.append(FieldDecl(fname, ftype, required, identifier, field_identity, fstart.line))
        self.expect("}")
        return EntityDecl(name, tuple(fields), identity, start.line)

    def parse_role_list(self) -> tuple[RoleDecl, ...]:
        roles: list[RoleDecl] = []
        self.expect("(")
        if not self.peek(")"):
            while True:
                r = self.tokens[self.i]
                name = self.ident().value
                self.expect(":")
                typ = self.ident().value
                identity = self.optional_identity()
                roles.append(RoleDecl(name, typ, identity, r.line))
                if self.peek(","):
                    self.pop()
                    continue
                break
        self.expect(")")
        return tuple(roles)

    def parse_names_call(self, keyword: str) -> tuple[tuple[str, ...], int]:
        start = self.expect(keyword)
        self.expect("(")
        names: list[str] = []
        if not self.peek(")"):
            while True:
                names.append(self.ident().value)
                if self.peek(","):
                    self.pop()
                    continue
                break
        self.expect(")")
        return tuple(names), start.line

    def parse_frequency(self) -> FrequencyDecl:
        start = self.expect("frequency")
        self.expect("(")
        args: list[Token] = []
        if not self.peek(")"):
            while True:
                t = self.tokens[self.i]
                if t.kind not in {"IDENT", "NUMBER"}:
                    raise ParseError(f"line {t.line}: frequency() expects role names followed by min,max integers")
                args.append(self.pop())
                if self.peek(","):
                    self.pop()
                    continue
                break
        self.expect(")")
        if len(args) < 3 or args[-1].kind != "NUMBER" or args[-2].kind != "NUMBER":
            raise ParseError(f"line {start.line}: frequency() requires one or more roles followed by min,max")
        if any(t.kind != "IDENT" for t in args[:-2]):
            raise ParseError(f"line {start.line}: frequency() role arguments must be names")
        if "." in args[-1].value or "." in args[-2].value:
            raise ParseError(f"line {start.line}: frequency bounds must be integers")
        return FrequencyDecl(
            tuple(t.value for t in args[:-2]), int(args[-2].value), int(args[-1].value), start.line
        )

    def parse_fact(self) -> FactDecl:
        start = self.expect("fact")
        name = self.ident().value
        identity = self.optional_identity()
        roles = self.parse_role_list()
        objectify_name: str | None = None
        objectify_identity: str | None = None
        if self.peek("objectify"):
            self.pop()
            objectify_name = self.ident().value
            objectify_identity = self.optional_identity()
        self.expect("{")
        fields: list[FieldDecl] = []
        reading: str | None = None
        uniques: list[UniqueDecl] = []
        mandatories: list[MandatoryDecl] = []
        frequencies: list[FrequencyDecl] = []
        unordered: list[UnorderedDecl] = []
        rings: list[RingDecl] = []
        while not self.peek("}"):
            if self.peek("reading"):
                self.pop()
                s = self.expect(kind="STRING")
                try:
                    reading = py_ast.literal_eval(s.value)
                except Exception as exc:
                    raise ParseError(f"line {s.line}: invalid string literal") from exc
            elif self.peek("unique"):
                names, line = self.parse_names_call("unique")
                uniques.append(UniqueDecl(names, line))
            elif self.peek("mandatory"):
                names, line = self.parse_names_call("mandatory")
                if len(names) != 1:
                    raise ParseError(f"line {line}: mandatory() takes exactly one role")
                mandatories.append(MandatoryDecl(names[0], line))
            elif self.peek("frequency"):
                frequencies.append(self.parse_frequency())
            elif self.peek("unordered"):
                names, line = self.parse_names_call("unordered")
                unordered.append(UnorderedDecl(names, line))
            elif self.peek("symmetric"):
                token = self.pop()
                rings.append(RingDecl("symmetric", token.line))
            else:
                fstart = self.tokens[self.i]
                fname = self.ident().value
                self.expect(":")
                ftype = self.ident().value
                required = True
                if self.peek("?"):
                    self.pop()
                    required = False
                field_identity = self.optional_identity()
                fields.append(FieldDecl(fname, ftype, required, False, field_identity, fstart.line))
        self.expect("}")
        return FactDecl(
            name,
            roles,
            tuple(fields),
            reading,
            tuple(uniques),
            tuple(mandatories),
            tuple(frequencies),
            tuple(unordered),
            tuple(rings),
            objectify_name,
            identity,
            objectify_identity,
            start.line,
        )

    def parse_role_sequence(self) -> RoleSequenceDecl:
        start = self.tokens[self.i]
        fact_name = self.ident().value
        self.expect("(")
        roles: list[str] = []
        if not self.peek(")"):
            while True:
                roles.append(self.ident().value)
                if self.peek(","):
                    self.pop()
                    continue
                break
        self.expect(")")
        if not roles:
            raise ParseError(f"line {start.line}: role sequence must contain at least one role")
        return RoleSequenceDecl(fact_name, tuple(roles), start.line)

    def parse_set_constraint(self) -> SetConstraintDecl:
        start = self.pop()
        kind = start.value
        left = self.parse_role_sequence()
        right = self.parse_role_sequence()
        return SetConstraintDecl(kind, left, right, start.line)

    def parse_subtype(self) -> SubtypeDecl:
        start = self.expect("subtype")
        sub = self.ident().value
        self.expect("is")
        sup = self.ident().value
        return SubtypeDecl(sub, sup, start.line)

    def parse_sample(self) -> SampleDecl:
        start = self.expect("sample")
        fact_name = self.ident().value
        self.expect("(")
        values: list[str] = []
        if not self.peek(")"):
            while True:
                t = self.tokens[self.i]
                if t.kind not in {"STRING", "IDENT", "NUMBER"}:
                    raise ParseError(f"line {t.line}: sample value must be a string, identifier, or number")
                self.pop()
                if t.kind == "STRING":
                    values.append(str(py_ast.literal_eval(t.value)))
                else:
                    values.append(t.value)
                if self.peek(","):
                    self.pop()
                    continue
                break
        self.expect(")")
        return SampleDecl(fact_name, tuple(values), start.line)

    def parse_analysis(self) -> AnalysisDecl:
        start = self.expect("analysis")
        name = self.ident().value
        args: list[str] = []
        if self.peek("("):
            self.pop()
            if not self.peek(")"):
                while True:
                    t = self.tokens[self.i]
                    if t.kind not in {"STRING", "IDENT", "NUMBER"}:
                        raise ParseError(f"line {t.line}: unsupported analysis argument")
                    self.pop()
                    args.append(str(py_ast.literal_eval(t.value)) if t.kind == "STRING" else t.value)
                    if self.peek(","):
                        self.pop()
                        continue
                    break
            self.expect(")")
        return AnalysisDecl(name, tuple(args), start.line)


def parse_model(text: str) -> ModelAst:
    return Parser(text).parse()
