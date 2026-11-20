# V3 (images): produce a PERTURBED 5_1_grt.odb for the same layout: re-run global routing with a
# different layer adjustment, so stored GR usage changes while the gcell grid / placement do not.
read_db $::env(SRC_ODB)
set_routing_layers -signal metal2-metal10 -clock metal4-metal10
set_global_routing_layer_adjustment metal2-metal10 0.15
global_route -allow_congestion
write_db $::env(DST_ODB)
puts "REGR_OK"
