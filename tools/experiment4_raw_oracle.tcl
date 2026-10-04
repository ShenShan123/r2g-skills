# Independent OpenDB export; converter code is never sourced.
foreach lef [split $::env(EXP4_ORACLE_LEFS) "\n"] { read_lef $lef }
read_def $::env(EXP4_ORACLE_DEF)
set block [ord::get_db_block]
set scale [$block getDbUnitsPerMicron]
set die [$block getDieArea]
set out [open $::env(EXP4_ORACLE_OUTPUT) w]
proc csv_row {handle values} {
    set quoted {}
    foreach value $values {
        lappend quoted "\"[string map {\" \"\"} $value]\""
    }
    puts $handle [join $quoted ,]
}
csv_row $out {kind name master status is_block x_um y_um center_x_um center_y_um width height area dbu pin_name valid net_name}
set width [expr {double([$die xMax] - [$die xMin]) / $scale}]
set height [expr {double([$die yMax] - [$die yMin]) / $scale}]
csv_row $out [list die {} {} {} {} [expr {double([$die xMin])/$scale}] \
    [expr {double([$die yMin])/$scale}] {} {} $width $height [expr {$width * $height}] $scale {} {} {}]
foreach inst [$block getInsts] {
    set master [$inst getMaster]
    set box [$inst getBBox]
    lassign [$inst getLocation] x y
    csv_row $out [list instance [$inst getName] [$master getName] [$inst getPlacementStatus] [$master isBlock] \
        [expr {double($x)/$scale}] [expr {double($y)/$scale}] \
        [expr {double([$box xMin]+[$box xMax])/(2*$scale)}] \
        [expr {double([$box yMin]+[$box yMax])/(2*$scale)}] {} {} {} {} {} {} {}]
    foreach iterm [$inst getITerms] {
        lassign [$iterm getAvgXY] valid pin_x pin_y
        if {$valid} {
            set pin_x [expr {double($pin_x)/$scale}]
            set pin_y [expr {double($pin_y)/$scale}]
        } else {
            set pin_x {}
            set pin_y {}
        }
        set net [$iterm getNet]
        set net_name {}
        if {$net != "NULL"} { set net_name [$net getName] }
        csv_row $out [list iterm [$inst getName] [$master getName] [$inst getPlacementStatus] \
            [$master isBlock] $pin_x $pin_y {} {} {} {} {} {} [[$iterm getMTerm] getName] $valid $net_name]
    }
}
foreach bterm [$block getBTerms] {
    lassign [$bterm getFirstPinLocation] valid pin_x pin_y
    if {$valid} {
        set pin_x [expr {double($pin_x)/$scale}]
        set pin_y [expr {double($pin_y)/$scale}]
    } else {
        set pin_x {}
        set pin_y {}
    }
    set net [$bterm getNet]
    set net_name {}
    if {$net != "NULL"} { set net_name [$net getName] }
    csv_row $out [list bterm [$bterm getName] {} [$bterm getFirstPinPlacementStatus] {} \
        $pin_x $pin_y {} {} {} {} {} {} {} $valid $net_name]
}
close $out
