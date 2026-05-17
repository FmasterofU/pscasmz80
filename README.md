# Z80 Assembler

This repository now includes a pure Z80 assembler that reads a `.asm` source file and writes a raw `.bin` machine-code image.

## Usage

```bash
python /home/runner/work/pscasmz80/pscasmz80/z80asm.py program.asm
python /home/runner/work/pscasmz80/pscasmz80/z80asm.py program.asm output.bin
```

The assembler:

- builds its opcode table from `/home/runner/work/pscasmz80/pscasmz80/Z80 instructions list.txt`
- supports labels, forward references, `ORG`, `EQU`, `DB`, `DW`, `DS`, and `END`
- accepts decimal, hexadecimal (`0x10`, `$10`, `10H`), binary (`0b1010`, `1010B`), character, and expression operands
- fills gaps between `ORG` segments with zero bytes in the generated `.bin`

## Supported source features

- labels with `label:` or `label INSTRUCTION`
- `;` line comments
- indexed addressing such as `(IX+5)` and `(IY-2)`
- relative branches using labels or expressions
- strings in `DB`, for example `DB "HELLO",13,10`
