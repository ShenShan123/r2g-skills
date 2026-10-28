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


def test_whitespace_filename_keeps_hdl_suffix(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    text = 'module dut(input a, output b); assign b=a; endmodule\n'
    (source / 'dut - Copy.v').write_text(text)
    (source / 'design.json').write_text(json.dumps({'top': 'dut', 'files': ['dut - Copy.v']}))
    meta = project.create(source, tmp_path / 'project', 'sky130hd')
    assert len(meta['files']) == 1
    staged = Path(meta['files'][0])
    assert staged.suffix == '.v' and not any(c.isspace() for c in str(staged))
    assert staged.read_text() == text


def test_parent_memory_parameter_is_relocated_but_unrelated_string_is_not(tmp_path):
    source = tmp_path / 'input'
    source.mkdir()
    (source / 'coeff.hex').write_text('0a\n')
    (source / 'dut.v').write_text('''module mem #(parameter FILE="")();
reg [7:0] words[0:0]; initial $readmemh(FILE,words); endmodule
module other #(parameter FILE="")(); endmodule
module wrapper #(parameter ROM="coeff.hex")(); mem #(.FILE(ROM)) m(); endmodule
module dut; wrapper w(); other #(.FILE("coeff.hex")) o(); endmodule
''')
    meta = project.create(source, tmp_path / 'project', 'nangate45', top='dut')
    text = Path(meta['files'][0]).read_text()
    assert 'parameter ROM="' + str(tmp_path / 'project/rtl/coeff.hex') + '"' in text
    # Literal replacement must not accidentally rewrite an unrelated parameter.
    assert 'other #(.FILE("coeff.hex"))' in text


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
