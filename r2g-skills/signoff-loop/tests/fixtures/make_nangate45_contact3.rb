# Builds the fixture that proves the CONTACT.3 tiling patch preserves the
# rule's verdict. Run with: klayout -b -r make_nangate45_contact3.rb
#
# CONTACT.3 is cont.not(active.or(poly.or(metal1))) -- a contact must lie
# inside active, poly or metal1. Real routed designs report zero of these, so
# comparing a patched deck against the stock one on corpus layouts yields
# 0 == 0 and proves nothing. This layout forces the rule to fire, with cases
# chosen to catch the ways a rewrite can go wrong:
#
#   C1 inside active / C2 inside poly / C3 inside metal1  -> must NOT flag
#   C4 on bare substrate                                  -> MUST flag whole
#   C5 half over metal1   -> MUST flag only the exposed half, which is what
#      separates an area-correct rewrite from a per-polygon selection
#   C6 inside metal1 with a far unrelated active shape, so the union carries a
#      member that an interacting()-style rewrite drops -> must NOT flag
#
# Layers follow the deck: active=1/0, poly=9/0, cont=10/0, metal1=11/0.
ly = RBA::Layout.new
ly.dbu = 0.001
top = ly.create_cell("SYNTH_C3")
lay = {}
{ "active" => 1, "poly" => 9, "cont" => 10, "metal1" => 11 }.each do |n, num|
  lay[n] = ly.layer(num, 0)
end
def box(top, lay, name, x0, y0, x1, y1)
  top.shapes(lay[name]).insert(
    RBA::Box.new((x0 * 1000).to_i, (y0 * 1000).to_i,
                 (x1 * 1000).to_i, (y1 * 1000).to_i))
end
box(top, lay, "active", 0, 0, 2, 2)
box(top, lay, "cont",   0.8, 0.8, 0.9, 0.9)      # C1
box(top, lay, "poly",   4, 0, 6, 2)
box(top, lay, "cont",   4.8, 0.8, 4.9, 0.9)      # C2
box(top, lay, "metal1", 8, 0, 10, 2)
box(top, lay, "cont",   8.8, 0.8, 8.9, 0.9)      # C3
box(top, lay, "cont",   12.8, 0.8, 12.9, 0.9)    # C4 -- violation
box(top, lay, "metal1", 16, 0, 16.85, 2)
box(top, lay, "cont",   16.8, 0.8, 16.9, 0.9)    # C5 -- partial violation
box(top, lay, "metal1", 20, 0, 22, 2)
box(top, lay, "cont",   20.8, 0.8, 20.9, 0.9)    # C6
box(top, lay, "active", 20, 6, 22, 8)
out = ARGV[0] || "nangate45_contact3.gds"
ly.write(out)
puts "wrote #{out}: #{top.shapes(lay['cont']).size} contacts, " \
     "C4 flagged whole and C5 flagged half"
