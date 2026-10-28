import hashlib
import json
from pathlib import Path

from r2g_agent import project


def test_whitespace_source_directory_keeps_bytes_and_compilation_roles(tmp_path):
    source = tmp_path / 'input'
    folder = source / 'RSIC-V core'
    folder.mkdir(parents=True)
    rtl = b'module dut(input a, output b); assign b=a; endmodule\n'
    (folder / 'dut.v').write_bytes(rtl)
    (source / 'design.json').write_text(json.dumps({'top': 'dut', 'files': ['RSIC-V core/dut.v'],
                                                 'include_dirs': ['RSIC-V core']}))
    dest = tmp_path / 'project'
    meta = project.create(source, dest, 'sky130hd')
    assert all(not any(c.isspace() for c in p) for p in meta['files'] + meta['include_dirs'])
    assert len(meta['files']) == 1
    assert Path(meta['files'][0]).read_bytes() == rtl
    assert meta['source_path_map']['RSIC-V core/dut.v']
    assert (folder / 'dut.v').read_bytes() == rtl


def test_whitespace_directory_resolves_original_relative_rom_with_duplicates(tmp_path):
    source = tmp_path / 'input'
    folder = source / 'core files'
    folder.mkdir(parents=True)
    other = source / 'other'
    other.mkdir()
    (folder / 'rom.hex').write_text('0a\n')
    (other / 'rom.hex').write_text('ff\n')
    (folder / 'dut.v').write_text('module dut; reg[7:0] m[0:0]; initial $readmemh("rom.hex", m); endmodule\n')
    (source / 'design.json').write_text(json.dumps({'top': 'dut', 'files': ['core files/dut.v']}))
    dest = tmp_path / 'project'
    meta = project.create(source, dest, 'nangate45')
    assert not meta['missing_data_files']
    assert (dest / 'rtl/core files/rom.hex').read_text() == '0a\n'
