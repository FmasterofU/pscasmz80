# Z80 Assembler

This repository now includes a pure Z80 assembler that reads a `.asm` source file and writes a raw `.bin` machine-code image.

## Usage

```bash
python z80asm.py program.asm
python z80asm.py program.asm output.bin
```

The assembler:

- builds its opcode table from `Z80 instructions list.txt`
- supports labels, forward references, `ORG`, `FORG`, `EQU`, `DB`, `DW`, `DS`, and `END`
- accepts decimal, hexadecimal (`0x10`, `$10`, `10H`), binary (`0b1010`, `1010B`), character, and expression operands
- fills unwritten addresses between the lowest and highest assembled addresses with zero bytes

## Supported source features

- labels with `label:` or `label INSTRUCTION`
- `;` line comments
- indexed addressing such as `(IX+5)` and `(IY-2)`
- relative branches using labels or expressions
- strings in `DB`, for example `DB "HELLO",13,10`
- `FORG` to move the output file position without changing the logical assembly address

`ORG` changes the logical assembly address and resets the default output position to match it. `FORG` only moves where subsequent bytes are written in the generated `.bin`.
