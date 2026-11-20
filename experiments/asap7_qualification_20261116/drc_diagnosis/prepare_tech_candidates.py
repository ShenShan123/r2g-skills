"""Prepare isolated, stricter experimental LEFs from the pinned ORFS technology."""
from pathlib import Path
import hashlib, json

out = Path(__file__).resolve().parent
source = Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7_tech_1x_201209.lef')
original = source.read_text()
via12_old = 'VIA VIA12 Default\n  LAYER M1 ;\n RECT -0.009 -0.011 0.009 0.011 ;'
via12_new = 'VIA VIA12 Default\n  LAYER M1 ;\n RECT -0.009 -0.014 0.009 0.014 ;'
generate_old = 'VIARULE M2_M1 GENERATE DEFAULT\n  LAYER M1 ;\n ENCLOSURE 0.0 0.0 ;'
generate_new = 'VIARULE M2_M1 GENERATE DEFAULT\n  LAYER M1 ;\n ENCLOSURE 0.005 0.0 ;'
assert original.count(via12_old) == original.count(generate_old) == 1
v1 = original.replace(via12_old, via12_new).replace(generate_old, generate_new)
(out/'asap7_tech_v1_enclosure_candidate.lef').write_text(v1)

corner_old = ' PROPERTY LEF58_CORNERSPACING "CORNERSPACING CONVEXCORNER CORNERONLY 0.01 WIDTH 0.018 SPACING 0.018 ;" ;'
corner_new = ' PROPERTY LEF58_CORNERSPACING "CORNERSPACING CONVEXCORNER WIDTH 0.0 SPACING 0.020 ;" ;'
assert v1.count(corner_old) == 1
m1 = v1.replace(corner_old, corner_new)
# Supplemental end-of-line keepout encodes the minimum 25 nm tip-to-side
# and 31 nm tip-to-tip spacing. It is conservative for 27 nm-only cases.
marker = ' OFFSET 0.0 ;\n\nEND M1'
assert m1.count(marker) == 1
m1 = m1.replace(marker, ' PROPERTY LEF58_SPACING "SPACING 0.025 ENDOFLINE 0.03625 WITHIN 0.025 ENDTOEND 0.031 ;" ;\n'+marker)
(out/'asap7_tech_v1_m1_spacing_candidate.lef').write_text(m1)
manifest = {'source':str(source),'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),
    'changes':{'v1':'VIA12 M1 enclosure from 2 nm each end to 5 nm; generated V1 M1 enclosure 5 nm in long direction.',
               'm1':'M1 corner spacing 20 nm; EOL tip-to-side 25 nm, tip-to-tip 31 nm.'},
    'status':'Candidates; OpenROAD LEF parser check is required, physical flow/signoff pending.',
    'rule_basis':'Official ASAP7 DRM 201207a tables 3.12.1 and 3.13.1. No Calibre rule changed.'}
(out/'tech_candidate_provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps(manifest,indent=2))
