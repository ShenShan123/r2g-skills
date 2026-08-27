from __future__ import annotations

import normalize_sky130_lvs_spice as n


def test_layout_normalizes_special_nfet_and_magic_diode_units():
    source = (
        "X1 d g s b sky130_fd_pr__special_nfet_01v8 w=0.36 l=0.15\n"
        "D0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 "
        "pj=2.64e+06 area=4.347e+11\n"
    )
    output, counts = n.normalize_layout(source)
    assert "sky130_fd_pr__special_nfet_01v8" not in output
    assert "M1 d g s b sky130_fd_pr__nfet_01v8" in output
    assert "pj=2.64e+06u area=4.347e+11p" in output
    assert counts == {
        "special_nfet_to_nfet": 1,
        "layout_mos_x_to_m": 1,
        "diode_lines_checked": 1,
        "diode_pj_unit": 1,
        "diode_area_unit": 1,
    }


def test_layout_transform_is_idempotent():
    source = (
        "D0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5 "
        "pj=2.64e+06u area=4.347e+11p\n"
    )
    once, _ = n.normalize_layout(source)
    twice, counts = n.normalize_layout(once)
    assert twice == once
    assert counts["diode_pj_unit"] == counts["diode_area_unit"] == 0


def test_library_normalizes_conb_short_and_diode_primitive():
    source = (
        ".subckt sky130_fd_sc_hd__conb_1 VGND VNB VPB VPWR HI LO\n"
        "X0 VGND LO VNB short w=480000u l=45000u\n"
        ".ends\n"
        ".subckt sky130_fd_sc_hd__diode_2 DIODE VGND VNB VPB VPWR\n"
        "X0 VNB DIODE sky130_fd_pr__diode_pw2nd p=5.36e+06u a=4.347e+11p\n"
        ".ends\n"
    )
    output, counts = n.normalize_library(source)
    assert "X0 VGND LO sky130_fd_pr__res_generic_po w=480000u l=45000u" in output
    assert "D0 VNB DIODE sky130_fd_pr__diode_pw2nd_05v5" in output
    assert "pj=5.36e+06u area=4.347e+11p" in output
    assert counts == {
        "library_mos_model_qualified": 0,
        "short_to_poly_resistor": 1,
        "two_terminal_conb_short_to_poly_resistor": 0,
        "library_diode_x_to_d": 1,
    }


def test_library_normalizes_two_terminal_hs_conb_shorts_only_in_known_cell():
    source = (
        ".SUBCKT sky130_fd_sc_hs__conb_1 VGND VNB VPB VPWR HI LO\n"
        "rI12 VGND LO short\n"
        "rI11 HI VPWR short\n"
        ".ENDS sky130_fd_sc_hs__conb_1\n"
        ".SUBCKT unrelated A B\n"
        "r0 A B short\n"
        ".ENDS unrelated\n"
    )
    output, counts = n.normalize_library(source)
    assert (
        "XI12 VGND LO sky130_fd_pr__res_generic_po w=0.51 l=0.045" in output
    )
    assert (
        "XI11 HI VPWR sky130_fd_pr__res_generic_po w=0.51 l=0.045" in output
    )
    assert "r0 A B short" in output
    assert counts["short_to_poly_resistor"] == 2
    assert counts["two_terminal_conb_short_to_poly_resistor"] == 2

    twice, twice_counts = n.normalize_library(output)
    assert twice == output
    assert twice_counts["two_terminal_conb_short_to_poly_resistor"] == 0


def test_layout_and_library_use_one_mos_representation():
    layout = (
        "X0 d g s b sky130_fd_pr__nfet_01v8_lvt w=0.74 l=0.15\n"
        "X1 d g s b sky130_fd_pr__pfet_01v8 w=1.12 l=0.15\n"
    )
    library = (
        "MMN d g s b nfet_01v8_lvt w=0.74 l=0.15\n"
        "MMP d g s b pfet_01v8 w=1.12 l=0.15\n"
    )

    normalized_layout, layout_counts = n.normalize_layout(layout)
    normalized_library, library_counts = n.normalize_library(library)

    assert normalized_layout == (
        "M0 d g s b sky130_fd_pr__nfet_01v8_lvt w=0.74 l=0.15\n"
        "M1 d g s b sky130_fd_pr__pfet_01v8 w=1.12 l=0.15\n"
    )
    assert normalized_library == (
        "MMN d g s b sky130_fd_pr__nfet_01v8_lvt w=0.74 l=0.15\n"
        "MMP d g s b sky130_fd_pr__pfet_01v8 w=1.12 l=0.15\n"
    )
    assert layout_counts["layout_mos_x_to_m"] == 2
    assert library_counts["library_mos_model_qualified"] == 2

    layout_twice, layout_twice_counts = n.normalize_layout(normalized_layout)
    library_twice, library_twice_counts = n.normalize_library(normalized_library)
    assert layout_twice == normalized_layout
    assert library_twice == normalized_library
    assert layout_twice_counts["layout_mos_x_to_m"] == 0
    assert library_twice_counts["library_mos_model_qualified"] == 0
