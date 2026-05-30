
# OpenROAD Script for PDNSim (IR Drop Analysis)
# Usage: openroad run_pdnsim.tcl [def_file] [output_rpt]

if {[info exists ::env(PROJECT_ROOT)]} {
    set project_root $::env(PROJECT_ROOT)
} else {
    set script_path [info script]
    if {$script_path != ""} {
        set script_dir [file dirname [file normalize $script_path]]
    } else {
        set script_dir [pwd]
    }
    if {[file exists [file join $script_dir "NangateOpenCellLibrary_typical.lib"]]} {
        set project_root $script_dir
    } else {
        set parent_dir [file normalize [file join $script_dir ..]]
        if {[file exists [file join $parent_dir "NangateOpenCellLibrary_typical.lib"]]} {
            set project_root $parent_dir
        } else {
            set project_root $script_dir
        }
    }
}

# Resolved liberty list (space-separated absolute paths) from the orchestrator
# (run_labels.sh always sets R2G_LIB_FILES to the resolved per-platform libs).
# PDNSim needs cell power to compute current; without liberty IR drop is all-zero.
set lib_files {}
if {[info exists ::env(R2G_LIB_FILES)] && [string trim $::env(R2G_LIB_FILES)] != ""} {
    foreach lib $::env(R2G_LIB_FILES) {
        if {[file exists $lib]} { lappend lib_files $lib }
    }
}
set tech_lef [file join $project_root "NangateOpenCellLibrary.tech.lef"]
set macro_lef [file join $project_root "NangateOpenCellLibrary.macro.lef"]
set macro_mod_lef [file join $project_root "NangateOpenCellLibrary.macro.mod.lef"]
if {[info exists ::env(ORFS_LEF_DIR)]} {
    set orfs_lef_dir $::env(ORFS_LEF_DIR)
} else {
    set orfs_lef_dir [file join $project_root "orfs_lef"]
}

if {[info exists ::env(SUPPLY_VOLTAGE)]} {
    set supply_voltage $::env(SUPPLY_VOLTAGE)
} else {
    set supply_voltage 1.1
}

set odb_file ""
if {[info exists ::env(ODB_FILE)]} {
    set odb_file $::env(ODB_FILE)
}

# Determine DEF file path (only required when no usable ODB — the read branch
# below errors if neither an ODB nor a DEF is available).
set def_file ""
if {[info exists ::env(DEF_FILE)]} {
    set def_file $::env(DEF_FILE)
} elseif {[llength $argv] > 0} {
    set def_file [lindex $argv 0]
} else {
    set default_def [file join $project_root "6_final.def"]
    if {[file exists $default_def]} {
        set def_file $default_def
    }
}

# Determine Output Report path
if {[info exists ::env(OUTPUT_RPT)]} {
    set out_file $::env(OUTPUT_RPT)
} elseif {[llength $argv] > 1} {
    set out_file [lindex $argv 1]
} else {
    set out_file "ir_drop.csv"
}

if {$odb_file != "" && [file exists $odb_file]} {
    puts "Reading ODB file: $odb_file"
    read_db $odb_file
    foreach lib $lib_files { read_liberty $lib }
} else {
    puts "Reading DEF file: $def_file"
    if {![file exists $def_file]} {
        puts "Error: DEF file $def_file does not exist."
        exit 1
    }
    foreach lib $lib_files { read_liberty $lib }
    read_lef $tech_lef
    read_lef $macro_lef
    read_lef $macro_mod_lef
    if {[file isdirectory $orfs_lef_dir]} {
        foreach lef [glob -nocomplain -directory $orfs_lef_dir fakeram45_*.lef] {
            read_lef $lef
        }
    }
    read_def $def_file
}

# Get Design Name
set db [::ord::get_db]
set chip [$db getChip]
set block [$chip getBlock]
set design_name [$block getName]
if {[info exists ::env(DESIGN_NAME)]} {
    set design_name $::env(DESIGN_NAME)
}

puts "Design loaded: $design_name"

set target_net ""
foreach n {VDD VPWR vdd vpwr} {
    if {[get_nets -quiet $n] != ""} {
        set target_net $n
        break
    }
}

if {$target_net == ""} {
    puts "Error: No power net found (tried VDD/VPWR)."
    exit 1
}

puts "Starting IR Drop Analysis on $target_net..."
# PDNSim needs the rail voltage explicitly (else PSM-0079). Mirror ORFS
# final_report.tcl: set power-net voltage, and ground (VSS/VGND) to 0.
catch {set_pdnsim_net_voltage -net $target_net -voltage $supply_voltage}
foreach gnd {VSS VGND vss vgnd} {
    if {[get_nets -quiet $gnd] != ""} {
        catch {set_pdnsim_net_voltage -net $gnd -voltage 0.0}
        break
    }
}
if {[catch {analyze_power_grid -net $target_net -voltage_file $out_file} error_msg]} {
    puts "Error during analyze_power_grid: $error_msg"
    exit 1
} else {
    set in_f [open $out_file r]
    set rows {}
    set drop_values {}
    set line_no 0
    while {[gets $in_f line] >= 0} {
        incr line_no
        if {$line_no == 1} {
            continue
        }

        set fields [split $line ","]
        if {[llength $fields] != 6} {
            continue
        }

        set inst [string trim [lindex $fields 0]]
        if {[regexp -nocase {^(wire|FILLER_|PHY_EDGE|TAPCELL|ENDCAP)} $inst]} {
            continue
        }

        set x [string trim [lindex $fields 3]]
        set y [string trim [lindex $fields 4]]
        set voltage [string trim [lindex $fields 5]]
        if {[catch {expr {$voltage + 0.0}} voltage_val]} {
            continue
        }

        set ir_drop_mv [expr {($supply_voltage - $voltage_val) * 1000.0}]
        if {$ir_drop_mv < 0.0} {
            set ir_drop_mv 0.0
        }

        lappend rows [list $inst $x $y $voltage_val $ir_drop_mv]
        lappend drop_values $ir_drop_mv
    }
    close $in_f

    set p95_mv 0.0
    set row_count [llength $drop_values]
    if {$row_count > 0} {
        set sorted_drops [lsort -real $drop_values]
        set p95_idx [expr {int(ceil(0.95 * $row_count)) - 1}]
        if {$p95_idx < 0} {
            set p95_idx 0
        }
        if {$p95_idx >= $row_count} {
            set p95_idx [expr {$row_count - 1}]
        }
        set p95_mv [lindex $sorted_drops $p95_idx]
    }

    set has_irdrop [expr {$p95_mv >= 0.05}]
    if {$has_irdrop} {
        set has_irdrop_str "true"
    } else {
        set has_irdrop_str "false"
    }

    set out_f [open $out_file w]
    puts $out_f "Design,Cell,X,Y,Voltage_V,IR_Drop_mV,P95_mV,label,has_irdrop"
    foreach row $rows {
        lassign $row inst x y voltage_val ir_drop_mv
        if {$has_irdrop && $p95_mv > 0.0} {
            set label [expr {log(1.0 + ($ir_drop_mv / $p95_mv))}]
        } else {
            set label 0.0
        }
        puts $out_f "$design_name,$inst,$x,$y,[format %.6f $voltage_val],[format %.6f $ir_drop_mv],[format %.6f $p95_mv],[format %.9f $label],$has_irdrop_str"
    }
    close $out_f

    puts "IR Drop analysis complete. Results written to: $out_file"
    puts "Wrote $row_count filtered cell rows. P95 IR drop: [format %.6f $p95_mv] mV. has_irdrop: $has_irdrop_str"
}

exit
