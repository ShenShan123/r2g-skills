# runs inside the r2g-orfs container: export each selected run's 3_place.odb to /out/<design>/3_place.def
while IFS=$'\t' read -r d run cells; do
  o=/out/$d; mkdir -p $o
  [ -s $o/3_place.def ] && continue
  printf 'read_db %s\nwrite_def %s\n' "/runs/$(basename $run)/3_place.odb" "$o/3_place.def.tmp" > $o/export.tcl
  openroad -no_init -exit $o/export.tcl > $o/export.log 2>&1 && mv $o/3_place.def.tmp $o/3_place.def
done
