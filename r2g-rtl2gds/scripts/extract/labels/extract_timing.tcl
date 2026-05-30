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

puts "Project Root: $project_root"

# Resolved liberty list (space-separated absolute paths) from the orchestrator
# (run_labels.sh always sets R2G_LIB_FILES to the resolved per-platform libs).
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

if {[info exists ::env(CLOCK_PERIOD)]} {
    set clock_period $::env(CLOCK_PERIOD)
} else {
    set clock_period 10.0
}

set odb_file ""
set def_file ""
if {[info exists ::env(ODB_FILE)]} {
    set odb_file $::env(ODB_FILE)
}

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

# Determine Output CSV path
if {[info exists ::env(OUTPUT_CSV)]} {
    set out_file $::env(OUTPUT_CSV)
} elseif {[llength $argv] > 1} {
    set out_file [lindex $argv 1]
} else {
    set out_file "timing_features.csv"
}

puts "Reading DEF file: $def_file"
if {$odb_file != "" && [file exists $odb_file]} {
    puts "Reading ODB file: $odb_file"
    read_db $odb_file
    foreach lib $lib_files { read_liberty $lib }
} else {
    if {$def_file == ""} {
        puts "Error: No input design provided. Set ODB_FILE or DEF_FILE env var (or pass DEF path as argument)."
        exit 1
    }
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

set component_names {}
foreach inst [$block getInsts] {
    lappend component_names [$inst getName]
}

set clk_ports {}
if {[info exists ::env(CLOCK_PORT)] && [string trim $::env(CLOCK_PORT)] ne ""} {
    set clk_ports [get_ports -quiet $::env(CLOCK_PORT)]
}
# Fall back to a clk/clock name match if the explicit port matched nothing — a
# mismatched SDC clk_port_name must not silently disable clock detection.
if {[llength $clk_ports] == 0} {
    foreach p [get_ports -quiet *] {
        set pname [get_full_name $p]
        if {[regexp -nocase {(clk|clock)} $pname]} {
            lappend clk_ports $p
        }
    }
}

if {[llength $clk_ports] > 0} {
    foreach p $clk_ports {
        set pname [get_full_name $p]
        create_clock -name $pname -period $clock_period $p
    }
} else {
    puts "No clock-like port found. Skipping clock creation."
}

# Run timing analysis
puts "Updating timing..."
report_checks -path_delay max -digits 4 > /dev/null

puts "Writing timing features to $out_file..."
set f [open $out_file w]
puts $f "Design,Cell,Cell_Slack_ns,Path_Delay_ns,label,in_sta_path"

set pins [get_pins *]
set count 0
set valid_count 0
array set cell_slack {}

foreach pin $pins {
    set name [get_full_name $pin]
    
    # Initialize variables
    set slack "N/A"
    
    # 1. Slack (Setup/Max)
    if {![catch {get_property $pin slack_max} val]} {
        if {$val > -1e29 && $val < 1e29} { set slack $val }
    }

    if {$slack != "N/A"} {
        if {[regexp {^(.+)/[^/]+$} $name -> cell_name]} {
            if {![info exists cell_slack($cell_name)] || $slack < $cell_slack($cell_name)} {
                set cell_slack($cell_name) $slack
            }
        }
        incr valid_count
    }
    
    incr count
    if {$count % 5000 == 0} {
        puts "Processed $count pins..."
    }
}

set written_count 0
set in_path_count 0
foreach cell_name $component_names {
    if {[info exists cell_slack($cell_name)]} {
        set slack $cell_slack($cell_name)
        set path_delay [expr {$clock_period - $slack}]
        if {$path_delay < 0.0} {
            set path_delay 0.0
        }
        set label [expr {log(1.0 + $path_delay)}]
        set in_sta_path true
        incr in_path_count
        puts $f "$design_name,$cell_name,[format %.6f $slack],[format %.6f $path_delay],[format %.9f $label],$in_sta_path"
    } else {
        set slack "INF"
        set path_delay 0.0
        set label 0.0
        set in_sta_path false
        puts $f "$design_name,$cell_name,$slack,[format %.6f $path_delay],[format %.9f $label],$in_sta_path"
    }
    incr written_count
}

close $f
puts "Done. Processed $count pins, found $valid_count valid pin slacks, written $written_count cell entries ($in_path_count in STA paths)."
exit
