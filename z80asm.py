from __future__ import annotations

import argparse
import ast
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


REPO_ROOT = Path(__file__).resolve().parent
INSTRUCTION_LIST_PATH = REPO_ROOT / "Z80 instructions list.txt"

REG_BITS = {"B": 0, "C": 1, "D": 2, "E": 3, "H": 4, "L": 5, "A": 7}
DIRECTIVES = {"ORG", "FORG", "EQU", "DB", "DEFB", "BYTE", "DW", "DEFW", "WORD", "DS", "DEFS", "SPACE", "END"}
IMPLICIT_A_MNEMONICS = {"AND", "CP", "OR", "SBC", "SUB", "XOR"}
FIXED_NUMERIC_PATTERNS = {"0": 0, "1": 1, "2": 2, "8H": 0x08, "10H": 0x10, "18H": 0x18, "20H": 0x20, "28H": 0x28, "30H": 0x30, "38H": 0x38}
OPERAND_KEYWORDS = {"A", "AF", "AF'", "B", "BC", "C", "D", "DE", "E", "H", "HL", "I", "IX", "IY", "L", "M", "NC", "NZ", "P", "PE", "PO", "R", "SP", "Z"}
LOCAL_LABEL_SEPARATOR = "::"


class AssemblerError(Exception):
    pass


@dataclass(frozen=True)
class InstructionSpec:
    mnemonic: str
    operand_patterns: tuple[str, ...]
    opcode_tokens: tuple[str, ...]
    source_line: int

    @property
    def size(self) -> int:
        return len(self.opcode_tokens)


@dataclass(frozen=True)
class Statement:
    line_number: int
    label: str | None
    operator: str | None
    operands: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class ParsedOperand:
    kind: str
    value: str


@dataclass(frozen=True)
class AssemblyResult:
    binary: bytes
    start_address: int | None
    end_address: int | None


def parse_instruction_specs(path: Path) -> tuple[InstructionSpec, ...]:
    # Groups: instruction text, documented byte size, opcode byte/formula field, clock cycles, flag effects.
    pattern = re.compile(r"^\s*(.+?)\s{2,}(\d+)\s{2,}(.+?)\s+(\d+(?:/\d+)?)\s+([\-*01?PV]{6})\s{2,}")
    specs: list[InstructionSpec] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        match = pattern.match(line)
        if not match:
            continue
        mnemonic_field = " ".join(match.group(1).split())
        opcode_tokens = tuple(match.group(3).split())
        parts = mnemonic_field.split(None, 1)
        mnemonic = parts[0].upper()
        operands = tuple(part.strip() for part in parts[1].split(",")) if len(parts) > 1 else ()
        specs.append(InstructionSpec(mnemonic=mnemonic, operand_patterns=operands, opcode_tokens=opcode_tokens, source_line=line_number))
    return tuple(specs)


INSTRUCTION_SPECS = parse_instruction_specs(INSTRUCTION_LIST_PATH)
MNEMONICS = {spec.mnemonic for spec in INSTRUCTION_SPECS}
KNOWN_WORDS = MNEMONICS | DIRECTIVES


def strip_comment(line: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(line):
        if quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char in {"'", '"'}:
            quote = char
        elif char == ";":
            return line[:index]
    return line


def split_operands(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    items: list[str] = []
    current: list[str] = []
    depth = 0
    quote: str | None = None
    escaped = False
    for char in text:
        if quote:
            current.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            if depth == 0:
                raise AssemblerError("Unbalanced closing parenthesis in operand list")
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            items.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    items.append("".join(current).strip())
    return tuple(item for item in items if item)


def parse_source(source: str) -> list[Statement]:
    statements: list[Statement] = []
    # Labels allow letters, digits, underscore, dot, @, ? and $ after the first character.
    label_pattern = re.compile(r"^([A-Za-z_.@?][A-Za-z0-9_.@?$]*)\s*:\s*(.*)$")
    bare_label_pattern = re.compile(r"^([A-Za-z_.@?][A-Za-z0-9_.@?$]*)\s+([A-Za-z][A-Za-z0-9]*)\b(.*)$")
    for line_number, raw_line in enumerate(source.splitlines(), start=1):
        text = strip_comment(raw_line).strip()
        if not text:
            continue
        label: str | None = None
        match = label_pattern.match(text)
        if match:
            label, text = match.group(1), match.group(2).strip()
        elif bare_label_pattern.match(text):
            bare = bare_label_pattern.match(text)
            assert bare is not None
            candidate_label = bare.group(1)
            candidate_operator = bare.group(2).upper()
            if candidate_label.upper() not in KNOWN_WORDS and candidate_operator in KNOWN_WORDS:
                label = candidate_label
                text = f"{bare.group(2)}{bare.group(3)}".strip()
        if not text:
            statements.append(Statement(line_number=line_number, label=label, operator=None, operands=(), text=raw_line))
            continue
        parts = text.split(None, 1)
        operator = parts[0].upper()
        operands = split_operands(parts[1].strip()) if len(parts) > 1 else ()
        statements.append(Statement(line_number=line_number, label=label, operator=operator, operands=operands, text=raw_line))
    return statements


def decode_quoted_text(token: str) -> str:
    try:
        value = ast.literal_eval(token)
    except (SyntaxError, ValueError) as exc:
        raise AssemblerError(f"Invalid string literal {token!r}") from exc
    if not isinstance(value, str):
        raise AssemblerError(f"Expected string literal, got {token!r}")
    return value


def parse_number_literal(token: str) -> int:
    upper = token.upper()
    if upper.startswith("$") and len(token) > 1:
        return int(token[1:], 16)
    if upper.startswith("0X"):
        return int(token, 16)
    if upper.startswith("0B"):
        return int(token, 2)
    if token[0].isdigit() and upper.endswith("H"):
        return int(token[:-1], 16)
    if token[0].isdigit() and upper.endswith("B"):
        return int(token[:-1], 2)
    if token[0].isdigit() and upper.endswith(("O", "Q")):
        return int(token[:-1], 8)
    return int(token, 10)


def tokenize_expression(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        if text[index] in {"'", '"'}:
            quote = text[index]
            start = index
            index += 1
            escaped = False
            while index < len(text):
                char = text[index]
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    index += 1
                    break
                index += 1
            else:
                raise AssemblerError(f"Unterminated string literal in expression {text!r}")
            tokens.append(("STRING", text[start:index]))
            continue
        if text.startswith("<<", index) or text.startswith(">>", index):
            tokens.append(("OP", text[index:index + 2]))
            index += 2
            continue
        if text[index] in "+-*/%&|^~()":
            tokens.append(("OP", text[index]))
            index += 1
            continue
        # Supported numeric formats: $FF, 0xFF, 0b1010, 10H, 1010B, 17Q/17O, and plain decimal.
        match = re.match(r"\$[0-9A-Fa-f]+|0[xX][0-9A-Fa-f]+|0[bB][01]+|[0-9][0-9A-Fa-f]*[HhBbQqOo]?|[0-9]+", text[index:])
        if match:
            tokens.append(("NUMBER", match.group(0)))
            index += len(match.group(0))
            continue
        match = re.match(r"[A-Za-z_.@?$][A-Za-z0-9_.@?$]*", text[index:])
        if match:
            tokens.append(("IDENT", match.group(0)))
            index += len(match.group(0))
            continue
        raise AssemblerError(f"Invalid expression token near {text[index:]!r}")
    return tokens


class ExpressionParser:
    def __init__(self, text: str, symbol_resolver: Callable[[str], int], current_address: int):
        self.tokens = tokenize_expression(text)
        self.index = 0
        self.symbol_resolver = symbol_resolver
        self.current_address = current_address

    def parse(self) -> int:
        value = self.parse_or()
        if self.index != len(self.tokens):
            raise AssemblerError(f"Unexpected token {self.tokens[self.index][1]!r}")
        return value

    def parse_or(self) -> int:
        value = self.parse_xor()
        while self.match("|"):
            value |= self.parse_xor()
        return value

    def parse_xor(self) -> int:
        value = self.parse_and()
        while self.match("^"):
            value ^= self.parse_and()
        return value

    def parse_and(self) -> int:
        value = self.parse_shift()
        while self.match("&"):
            value &= self.parse_shift()
        return value

    def parse_shift(self) -> int:
        value = self.parse_add_sub()
        while True:
            if self.match("<<"):
                value <<= self.parse_add_sub()
            elif self.match(">>"):
                value >>= self.parse_add_sub()
            else:
                return value

    def parse_add_sub(self) -> int:
        value = self.parse_mul_div()
        while True:
            if self.match("+"):
                value += self.parse_mul_div()
            elif self.match("-"):
                value -= self.parse_mul_div()
            else:
                return value

    def parse_mul_div(self) -> int:
        value = self.parse_unary()
        while True:
            if self.match("*"):
                value *= self.parse_unary()
            elif self.match("/"):
                divisor = self.parse_unary()
                if divisor == 0:
                    raise AssemblerError("Division by zero")
                value //= divisor
            elif self.match("%"):
                divisor = self.parse_unary()
                if divisor == 0:
                    raise AssemblerError("Modulo by zero")
                value %= divisor
            else:
                return value

    def parse_unary(self) -> int:
        if self.match("+"):
            return +self.parse_unary()
        if self.match("-"):
            return -self.parse_unary()
        if self.match("~"):
            return ~self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> int:
        if self.match("("):
            value = self.parse_or()
            self.expect(")")
            return value
        if self.index >= len(self.tokens):
            raise AssemblerError("Unexpected end of expression")
        kind, token = self.tokens[self.index]
        self.index += 1
        if kind == "NUMBER":
            return parse_number_literal(token)
        if kind == "STRING":
            text = decode_quoted_text(token)
            if len(text) != 1:
                raise AssemblerError("Character literals in expressions must contain exactly one character")
            return ord(text)
        if kind == "IDENT":
            if token == "$":
                return self.current_address
            return self.symbol_resolver(token)
        raise AssemblerError(f"Unexpected token {token!r}")

    def match(self, operator: str) -> bool:
        if self.index < len(self.tokens) and self.tokens[self.index] == ("OP", operator):
            self.index += 1
            return True
        return False

    def expect(self, operator: str) -> None:
        if not self.match(operator):
            raise AssemblerError(f"Expected {operator!r}")


def evaluate_expression(text: str, symbol_resolver: Callable[[str], int], current_address: int) -> int:
    return ExpressionParser(text, symbol_resolver, current_address).parse()


def normalize_operand_text(text: str) -> str:
    return re.sub(r"\s+", "", text).upper()


def parse_operand(text: str) -> ParsedOperand:
    stripped = text.strip()
    normalized = normalize_operand_text(stripped)
    if normalized.startswith("(") and normalized.endswith(")"):
        inner = normalized[1:-1]
        if inner in {"BC", "C", "DE", "HL", "SP"}:
            return ParsedOperand("token", f"({inner})")
        if inner == "IX":
            return ParsedOperand("indexed", "IX:0")
        if inner == "IY":
            return ParsedOperand("indexed", "IY:0")
        if inner.startswith("IX+") or inner.startswith("IX-"):
            return ParsedOperand("indexed", f"IX:{stripped[1:-1][2:].strip()}")
        if inner.startswith("IY+") or inner.startswith("IY-"):
            return ParsedOperand("indexed", f"IY:{stripped[1:-1][2:].strip()}")
        return ParsedOperand("indirect", stripped[1:-1].strip())
    if normalized in OPERAND_KEYWORDS:
        return ParsedOperand("token", normalized)
    return ParsedOperand("expr", stripped)


def is_formula_token(token: str) -> bool:
    return any(marker in token for marker in ("rb", "b", "+", "*"))


def try_evaluate_constant(text: str) -> int | None:
    def unresolved_symbol(symbol: str) -> int:
        raise AssemblerError(f"Unknown symbol {symbol}")

    try:
        return evaluate_expression(text, unresolved_symbol, 0)
    except AssemblerError:
        return None


def operand_matches(pattern: str, operand: ParsedOperand) -> bool:
    if pattern == "r":
        return operand.kind == "token" and operand.value in REG_BITS
    if pattern == "b":
        if operand.kind != "expr":
            return False
        value = try_evaluate_constant(operand.value)
        return value is not None and 0 <= value <= 7
    if pattern in {"N", "NN", "n"}:
        return operand.kind == "expr"
    if pattern in {"(N)", "(NN)"}:
        return operand.kind == "indirect"
    if pattern in {"(IX+n)", "(IY+n)"}:
        return operand.kind == "indexed" and operand.value.startswith(pattern[1:3])
    if pattern in {"(IX)", "(IY)"}:
        return operand.kind == "indexed" and operand.value == f"{pattern[1:3]}:0"
    if pattern in FIXED_NUMERIC_PATTERNS:
        if operand.kind != "expr":
            return False
        value = try_evaluate_constant(operand.value)
        return value == FIXED_NUMERIC_PATTERNS[pattern]
    return operand.kind == "token" and operand.value == pattern.upper()


def find_instruction_spec(statement: Statement) -> tuple[InstructionSpec, tuple[ParsedOperand, ...]]:
    assert statement.operator is not None
    parsed_operands = tuple(parse_operand(operand) for operand in statement.operands)
    candidate_sets = [parsed_operands]
    if statement.operator in IMPLICIT_A_MNEMONICS and len(parsed_operands) >= 2 and parsed_operands[0] == ParsedOperand("token", "A"):
        candidate_sets.append(parsed_operands[1:])
    for candidate_operands in candidate_sets:
        for spec in INSTRUCTION_SPECS:
            if spec.mnemonic != statement.operator or len(spec.operand_patterns) != len(candidate_operands):
                continue
            if all(operand_matches(pattern, operand) for pattern, operand in zip(spec.operand_patterns, candidate_operands)):
                return spec, tuple(candidate_operands)
    operand_text = ", ".join(statement.operands)
    raise AssemblerError(f"Line {statement.line_number}: unsupported instruction {statement.operator} {operand_text}".rstrip())


def encode_formula(token: str, values: dict[str, int]) -> int:
    total = 0
    for term in token.split("+"):
        product = 1
        for factor in term.split("*"):
            factor = factor.strip()
            product *= values[factor] if factor in values else int(factor, 16)
        total += product
    return total & 0xFF


def coerce_byte(value: int, *, signed: bool, line_number: int, description: str) -> int:
    if signed:
        if not -128 <= value <= 127:
            raise AssemblerError(f"Line {line_number}: {description} out of signed 8-bit range: {value}")
    elif not 0 <= value <= 0xFF:
        raise AssemblerError(f"Line {line_number}: {description} out of 8-bit range: {value}")
    return value & 0xFF


def coerce_word(value: int, *, line_number: int, description: str) -> int:
    if not -0x8000 <= value <= 0xFFFF:
        raise AssemblerError(f"Line {line_number}: {description} out of 16-bit range: {value}")
    return value & 0xFFFF


def validate_output_position(position: int, *, line_number: int, directive: str) -> int:
    if position < 0:
        raise AssemblerError(f"Line {line_number}: {directive} position cannot be negative")
    return position


class Z80Assembler:
    def __init__(self) -> None:
        self.specs = INSTRUCTION_SPECS

    def assemble_text(self, source: str) -> AssemblyResult:
        statements = parse_source(source)
        symbols = self._build_symbol_table(statements)
        return self._encode(statements, symbols)

    def assemble_file(self, source_path: Path) -> AssemblyResult:
        return self.assemble_text(source_path.read_text(encoding="utf-8"))

    def _qualify_label(self, label: str, current_global: str | None, line_number: int) -> str:
        if not label.startswith("."):
            return label
        if current_global is None:
            raise AssemblerError(f"Line {line_number}: local label {label} has no parent global label")
        return f"{current_global}{LOCAL_LABEL_SEPARATOR}{label}"

    def _resolve_symbol(
        self,
        name: str,
        labels: dict[str, int],
        equ_map: dict[str, tuple[str, str | None]],
        current_global: str | None,
        line_number: int,
        stack: set[str] | None = None,
    ) -> int:
        canonical_name = self._qualify_label(name, current_global, line_number) if name.startswith(".") else name
        if canonical_name in labels:
            return labels[canonical_name]
        if canonical_name in equ_map:
            return self._resolve_equ(canonical_name, labels, equ_map, stack or set())
        raise AssemblerError(f"Unknown symbol {name}")

    def _build_symbol_table(self, statements: list[Statement]) -> dict[str, int]:
        symbols: dict[str, int] = {}
        equ_map: dict[str, tuple[str, str | None]] = {}
        pc = 0
        current_global: str | None = None

        for statement in statements:
            if statement.operator == "END":
                break
            if statement.label and not statement.label.startswith("."):
                current_global = statement.label
            qualified_label = self._qualify_label(statement.label, current_global, statement.line_number) if statement.label else None

            def resolve_visible(name: str, scope: str | None = current_global, line_number: int = statement.line_number) -> int:
                return self._resolve_symbol(name, symbols, equ_map, scope, line_number)

            if statement.operator == "EQU":
                if qualified_label is None:
                    raise AssemblerError(f"Line {statement.line_number}: EQU requires a label")
                if len(statement.operands) != 1:
                    raise AssemblerError(f"Line {statement.line_number}: EQU requires exactly one operand")
                if qualified_label in symbols or qualified_label in equ_map:
                    raise AssemblerError(f"Line {statement.line_number}: duplicate label {statement.label}")
                equ_map[qualified_label] = (statement.operands[0], current_global)
                continue
            if qualified_label:
                if qualified_label in symbols or qualified_label in equ_map:
                    raise AssemblerError(f"Line {statement.line_number}: duplicate label {statement.label}")
                symbols[qualified_label] = pc
            if statement.operator is None:
                continue
            if statement.operator == "ORG":
                if len(statement.operands) != 1:
                    raise AssemblerError(f"Line {statement.line_number}: ORG requires exactly one operand")
                pc = evaluate_expression(statement.operands[0], resolve_visible, pc)
                continue
            if statement.operator == "FORG":
                if len(statement.operands) != 1:
                    raise AssemblerError(f"Line {statement.line_number}: FORG requires exactly one operand")
                # FORG only changes the output file position; logical addresses and labels stay on the ORG-driven PC.
                validate_output_position(
                    evaluate_expression(statement.operands[0], resolve_visible, pc),
                    line_number=statement.line_number,
                    directive="FORG",
                )
                continue
            if statement.operator in {"DB", "DEFB", "BYTE"}:
                pc += self._db_size(statement)
                continue
            if statement.operator in {"DW", "DEFW", "WORD"}:
                if any(operand[:1] in {"'", '"'} for operand in statement.operands):
                    raise AssemblerError(f"Line {statement.line_number}: strings are only supported in DB directives, not {statement.operator}")
                pc += 2 * len(statement.operands)
                continue
            if statement.operator in {"DS", "DEFS", "SPACE"}:
                if not 1 <= len(statement.operands) <= 2:
                    raise AssemblerError(f"Line {statement.line_number}: DS requires one or two operands")
                count = evaluate_expression(statement.operands[0], resolve_visible, pc)
                if count < 0:
                    raise AssemblerError(f"Line {statement.line_number}: DS count cannot be negative")
                pc += count
                continue
            if statement.operator in DIRECTIVES:
                continue
            spec, _ = find_instruction_spec(statement)
            pc += spec.size

        resolved = dict(symbols)
        for name in equ_map:
            resolved[name] = self._resolve_equ(name, symbols, equ_map, set())
        return resolved

    def _resolve_equ(self, name: str, labels: dict[str, int], equ_map: dict[str, tuple[str, str | None]], stack: set[str]) -> int:
        if name in labels:
            return labels[name]
        if name in stack:
            raise AssemblerError(f"Cyclic EQU definition involving {name}")
        if name not in equ_map:
            raise AssemblerError(f"Unknown symbol {name}")
        stack.add(name)
        expression, current_global = equ_map[name]

        def resolver(symbol: str) -> int:
            return self._resolve_symbol(symbol, labels, equ_map, current_global, 0, stack)

        value = evaluate_expression(expression, resolver, 0)
        stack.remove(name)
        labels[name] = value
        return value

    def _db_size(self, statement: Statement) -> int:
        size = 0
        for operand in statement.operands:
            stripped = operand.strip()
            if stripped[:1] in {"'", '"'}:
                size += len(decode_quoted_text(stripped))
            else:
                size += 1
        return size

    def _encode(self, statements: list[Statement], symbols: dict[str, int]) -> AssemblyResult:
        # Keys are output file offsets, not logical CPU addresses; logical address evaluation stays on `pc`.
        output_buffer: dict[int, int] = {}
        pc = 0
        output_offset = 0
        lowest: int | None = None
        highest: int | None = None
        current_global: str | None = None

        def emit(data: list[int], line_number: int) -> None:
            nonlocal pc, output_offset, lowest, highest
            for offset, byte in enumerate(data):
                output_position = output_offset + offset
                if output_position in output_buffer:
                    raise AssemblerError(f"Line {line_number}: output position 0x{output_position:04X} written more than once")
                output_buffer[output_position] = byte & 0xFF
            if data:
                lowest = output_offset if lowest is None else min(lowest, output_offset)
                highest = output_offset + len(data) if highest is None else max(highest, output_offset + len(data))
                pc += len(data)
                output_offset += len(data)

        for statement in statements:
            if statement.operator == "END":
                break
            if statement.label and not statement.label.startswith("."):
                current_global = statement.label

            def symbol_resolver(name: str, scope: str | None = current_global, line_number: int = statement.line_number) -> int:
                return self._resolve_symbol(name, symbols, {}, scope, line_number)

            if statement.operator in {None, "EQU"}:
                continue
            if statement.operator == "ORG":
                pc = evaluate_expression(statement.operands[0], symbol_resolver, pc)
                # ORG changes both the logical address and the default output position.
                output_offset = pc
                continue
            if statement.operator == "FORG":
                # FORG only repositions output bytes in the .bin file without affecting logical addresses.
                output_offset = validate_output_position(
                    evaluate_expression(statement.operands[0], symbol_resolver, pc),
                    line_number=statement.line_number,
                    directive="FORG",
                )
                continue
            if statement.operator in {"DB", "DEFB", "BYTE"}:
                data: list[int] = []
                for operand in statement.operands:
                    stripped = operand.strip()
                    if stripped[:1] in {"'", '"'}:
                        data.extend(ord(char) for char in decode_quoted_text(stripped))
                    else:
                        value = evaluate_expression(stripped, symbol_resolver, pc + len(data))
                        data.append(coerce_byte(value, signed=False, line_number=statement.line_number, description="DB value"))
                emit(data, statement.line_number)
                continue
            if statement.operator in {"DW", "DEFW", "WORD"}:
                data = []
                for operand in statement.operands:
                    value = coerce_word(evaluate_expression(operand, symbol_resolver, pc + len(data)), line_number=statement.line_number, description="DW value")
                    data.extend((value & 0xFF, (value >> 8) & 0xFF))
                emit(data, statement.line_number)
                continue
            if statement.operator in {"DS", "DEFS", "SPACE"}:
                count = evaluate_expression(statement.operands[0], symbol_resolver, pc)
                fill = evaluate_expression(statement.operands[1], symbol_resolver, pc) if len(statement.operands) == 2 else 0
                emit([coerce_byte(fill, signed=False, line_number=statement.line_number, description="DS fill")] * count, statement.line_number)
                continue
            spec, parsed_operands = find_instruction_spec(statement)
            emit(self._encode_instruction(spec, parsed_operands, statement.line_number, pc, symbol_resolver), statement.line_number)

        if lowest is None or highest is None:
            return AssemblyResult(binary=b"", start_address=None, end_address=None)
        image = bytearray(highest - lowest)
        for address, value in output_buffer.items():
            image[address - lowest] = value
        return AssemblyResult(binary=bytes(image), start_address=lowest, end_address=highest)

    def _encode_instruction(
        self,
        spec: InstructionSpec,
        operands: tuple[ParsedOperand, ...],
        line_number: int,
        pc: int,
        symbol_resolver: Callable[[str], int],
    ) -> list[int]:
        values: dict[str, int] = {}
        placeholders: list[tuple[str, str]] = []
        indexed = next((operand for pattern, operand in zip(spec.operand_patterns, operands) if pattern in {"(IX+n)", "(IY+n)"}), None)
        if indexed:
            _, expr = indexed.value.split(":", 1)
            placeholders.append(("disp", expr or "0"))
        for pattern, operand in zip(spec.operand_patterns, operands):
            if pattern == "r":
                values["rb"] = REG_BITS[operand.value]
            elif pattern == "b":
                values["b"] = evaluate_expression(operand.value, symbol_resolver, pc)
            elif pattern == "N":
                placeholders.append(("byte", operand.value))
            elif pattern == "NN":
                placeholders.extend((("word_lo", operand.value), ("word_hi", operand.value)))
            elif pattern == "n":
                placeholders.append(("rel", operand.value))
            elif pattern == "(N)":
                placeholders.append(("byte", operand.value))
            elif pattern == "(NN)":
                placeholders.extend((("word_lo", operand.value), ("word_hi", operand.value)))
        emitted: list[int] = []
        next_pc = pc + spec.size
        for token in spec.opcode_tokens:
            if token == "XX":
                if not placeholders:
                    raise AssemblerError(f"Line {line_number}: internal placeholder mismatch")
                kind, expr = placeholders.pop(0)
                value = evaluate_expression(expr, symbol_resolver, pc)
                if kind == "disp":
                    emitted.append(coerce_byte(value, signed=True, line_number=line_number, description="Index displacement"))
                elif kind == "byte":
                    emitted.append(coerce_byte(value, signed=False, line_number=line_number, description="Immediate byte"))
                elif kind == "word_lo":
                    word = coerce_word(value, line_number=line_number, description="Immediate word")
                    emitted.append(word & 0xFF)
                elif kind == "word_hi":
                    word = coerce_word(value, line_number=line_number, description="Immediate word")
                    emitted.append((word >> 8) & 0xFF)
                elif kind == "rel":
                    displacement = value - next_pc
                    emitted.append(coerce_byte(displacement, signed=True, line_number=line_number, description="Relative jump"))
                else:
                    raise AssemblerError(f"Line {line_number}: unsupported placeholder kind {kind}")
                continue
            emitted.append(encode_formula(token, values) if is_formula_token(token) else int(token, 16))
        if placeholders:
            raise AssemblerError(f"Line {line_number}: internal placeholder mismatch")
        if len(emitted) != spec.size:
            raise AssemblerError(f"Line {line_number}: internal size mismatch")
        return emitted


def assemble_file(source_path: Path, output_path: Path) -> AssemblyResult:
    assembler = Z80Assembler()
    result = assembler.assemble_file(source_path)
    output_path.write_bytes(result.binary)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Assemble a Z80 .asm source file into a .bin image.")
    parser.add_argument("source", help="Path to the input .asm file")
    parser.add_argument("output", nargs="?", help="Path to the output .bin file (defaults to source with .bin suffix)")
    args = parser.parse_args(argv)
    source_path = Path(args.source)
    output_path = Path(args.output) if args.output else source_path.with_suffix(".bin")
    try:
        result = assemble_file(source_path, output_path)
    except AssemblerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if result.start_address is None:
        print(f"Wrote empty output to {output_path}")
    else:
        print(f"Wrote {len(result.binary)} bytes to {output_path} (origin 0x{result.start_address:04X})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
