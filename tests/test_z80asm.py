from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from z80asm import AssemblerError, Z80Assembler, main, parse_source


class Z80AssemblerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.assembler = Z80Assembler()

    def test_basic_assembly_with_common_features(self) -> None:
        source = """
            ORG 100H
        start:
            LD A,42H
            LD (IX+1),A
            BIT 3,(IY-2)
            JR NZ,start
            DB "OK",0
        """
        result = self.assembler.assemble_text(source)
        self.assertEqual(result.start_address, 0x100)
        self.assertEqual(
            result.binary,
            bytes([0x3E, 0x42, 0xDD, 0x77, 0x01, 0xFD, 0xCB, 0xFE, 0x5E, 0x20, 0xF5, 0x4F, 0x4B, 0x00]),
        )

    def test_supports_equ_db_dw_and_ds(self) -> None:
        source = """
            BASE EQU 4000H
            ORG BASE
        header:
            DB 'A',"BC",0
            DW header+4
            DS 2,0FFH
        """
        result = self.assembler.assemble_text(source)
        self.assertEqual(result.start_address, 0x4000)
        self.assertEqual(result.binary, bytes([0x41, 0x42, 0x43, 0x00, 0x04, 0x40, 0xFF, 0xFF]))

    def test_supports_percent_prefixed_binary_literals_without_breaking_modulo(self) -> None:
        source = """
            ORG %10
            DB %10101010
            DB 5%2
        """
        result = self.assembler.assemble_text(source)
        self.assertEqual(result.start_address, 0x2)
        self.assertEqual(result.binary, bytes([0xAA, 0x01]))

    def test_forg_changes_file_offset_without_changing_logical_addresses(self) -> None:
        source = """
            ORG 0
        start:
            JR next
            FORG 10H
        next:
            DB next-start
        """
        symbols = self.assembler._build_symbol_table(parse_source(source))
        jr_instruction_size = 2
        self.assertEqual(symbols["next"], jr_instruction_size)
        result = self.assembler.assemble_text(source)
        gap_size = 0x10 - jr_instruction_size
        self.assertEqual(result.start_address, 0)
        self.assertEqual(result.binary[:2], bytes([0x18, 0x00]))
        self.assertEqual(result.binary, bytes([0x18, 0x00] + ([0x00] * gap_size) + [jr_instruction_size]))

    def test_reuses_local_labels_under_different_global_labels(self) -> None:
        source = """
        first:
        .loop:
            JR .done
        .done:
        second:
        .loop:
            JR .done
        .done:
        """
        result = self.assembler.assemble_text(source)
        self.assertEqual(result.binary, bytes([0x18, 0x00, 0x18, 0x00]))

    def test_local_label_requires_parent_global_label(self) -> None:
        with self.assertRaisesRegex(AssemblerError, r"local label \.loop has no parent global label"):
            self.assembler.assemble_text(".loop:\nNOP\n")

    def test_cli_writes_default_bin_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            asm_path = Path(tmpdir) / "demo.asm"
            asm_path.write_text("ORG 0\nNOP\n", encoding="utf-8")
            exit_code = main([str(asm_path)])
            self.assertEqual(exit_code, 0)
            self.assertEqual(asm_path.with_suffix(".bin").read_bytes(), b"\x00")


if __name__ == "__main__":
    unittest.main()
