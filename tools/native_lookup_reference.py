#!/usr/bin/env python3
"""Optional research harness for one hash-pinned official Android x86_64 ELF.

Needs pyelftools (analysis environment only). Map the inspected library without
Android constructors and bind its eight imports to host libc/libm. This checks
arithmetic against actual APK instructions, not an independent temperature
measurement or an Android UI observation. Never import this from production.
"""

import argparse
import ctypes as C
import hashlib
import json
import mmap
from pathlib import Path
import platform
import struct

ELF_SHA256 = '00fc62661ec6d407ecc8b2dd14a1d136701a386bd8f4b9e1ee342f8b42e7addd'


class Reference:
    def __init__(self, path):
        from elftools.elf.elffile import ELFFile
        if platform.machine() != 'x86_64' or platform.system() != 'Linux':
            raise RuntimeError('This research harness requires Linux x86_64')
        if hashlib.sha256(path.read_bytes()).hexdigest() != ELF_SHA256:
            raise ValueError('Unexpected library hash; ABI and offsets are hash-specific')
        self.libs = [C.CDLL('libc.so.6'), C.CDLL('libm.so.6')]
        with path.open('rb') as file:
            elf = ELFFile(file)
            self.memory = mmap.mmap(-1, 0x6000, prot=mmap.PROT_READ | mmap.PROT_WRITE | mmap.PROT_EXEC)
            self.base = C.addressof(C.c_char.from_buffer(self.memory))
            for segment in elf.iter_segments():
                if segment['p_type'] == 'PT_LOAD':
                    start = segment['p_vaddr']
                    self.memory[start:start + segment['p_filesz']] = segment.data()
            symbols = elf.get_section_by_name('.dynsym')
            allowed = {'__cxa_finalize', '__cxa_atexit', 'pow', 'exp', 'sqrtf',
                       'sqrt', '__stack_chk_fail', 'puts'}
            for section_name in ('.rela.dyn', '.rela.plt'):
                for relocation in elf.get_section_by_name(section_name).iter_relocations():
                    if relocation['r_info_type'] == 8:  # R_X86_64_RELATIVE
                        address = self.base + relocation['r_addend']
                    elif relocation['r_info_type'] == 7:  # R_X86_64_JUMP_SLOT
                        name = symbols.get_symbol(relocation['r_info_sym']).name
                        if name not in allowed:
                            raise ValueError(f'Unreviewed ELF import: {name}')
                        function = next(getattr(lib, name) for lib in self.libs if hasattr(lib, name))
                        address = C.cast(function, C.c_void_p).value
                    else:
                        raise ValueError('Unsupported ELF relocation')
                    struct.pack_into('<Q', self.memory, relocation['r_offset'], address)
        pointer = C.c_void_p
        self.build = C.CFUNCTYPE(None, C.c_int, C.c_int, *([pointer] * 9),
                                C.c_int, C.c_int, C.c_float)(self.base + 0x1500)
        self.search = C.CFUNCTYPE(None, C.c_int, C.c_int, pointer, pointer,
                                 pointer, C.c_int, C.c_int)(self.base + 0x2040)

    def calculate(self, raw):
        if len(raw) != 224256:
            raise ValueError('Expected a complete HT-301 frame')
        frame = C.create_string_buffer(raw)
        table = (C.c_float * 16384)()
        parameters = [C.c_float() for _ in range(6)]
        distance = C.c_uint16()
        self.build(384, 292, table, C.addressof(frame) + 221184,
                   *[C.byref(p) for p in parameters], C.byref(distance), 68, 120, 1.5)
        output = (C.c_float * (10 + 384 * 288))(*([float('nan')] * (10 + 384 * 288)))
        self.search(384, 292, table, frame, output, 120, 4)
        return bytes(table), list(output[:10]), [p.value for p in parameters] + [distance.value]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('library', type=Path)
    parser.add_argument('frame', type=Path)
    parser.add_argument('--table', type=Path, required=True)
    args = parser.parse_args()
    table, summary, parameters = Reference(args.library).calculate(args.frame.read_bytes())
    args.table.write_bytes(table)
    print(json.dumps({'library_sha256': ELF_SHA256, 'summary': summary,
                      'parameters_fpa_correction_reflection_ambient_humidity_emissivity_distance': parameters,
                      'table_sha256': hashlib.sha256(table).hexdigest()}))


if __name__ == '__main__':
    main()
