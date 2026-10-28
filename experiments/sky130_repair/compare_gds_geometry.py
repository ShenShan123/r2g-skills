"""KLayout batch script: exact flattened polygons and transformed GDS labels.

Run inside the frozen image with -rd left=... -rd right=... -rd output=....
File timestamps and hierarchy organization are irrelevant to physical XOR.
"""
import hashlib
import json
import pya


def labels(cell,layer):
    values=[];iterator=cell.begin_shapes_rec(layer)
    while not iterator.at_end():
        shape=iterator.shape()
        if shape.is_text():
            text=shape.text.transformed(iterator.trans())
            values.append((text.string,text.trans.to_s()))
        iterator.next()
    return sorted(values)


a=pya.Layout();a.read(left)
b=pya.Layout();b.read(right)
if a.dbu!=b.dbu:raise ValueError('different GDS database units')
if len(a.top_cells())!=1 or len(b.top_cells())!=1:raise ValueError('ambiguous top cell')
ca=a.top_cells()[0];cb=b.top_cells()[0]
ia={(a.get_info(i).layer,a.get_info(i).datatype):i for i in a.layer_indexes()}
ib={(b.get_info(i).layer,b.get_info(i).datatype):i for i in b.layer_indexes()}
layers=[]
for key in sorted(set(ia)|set(ib)):
    ra=pya.Region(ca.begin_shapes_rec(ia[key])) if key in ia else pya.Region()
    rb=pya.Region(cb.begin_shapes_rec(ib[key])) if key in ib else pya.Region()
    delta=ra^rb
    la=labels(ca,ia[key]) if key in ia else []
    lb=labels(cb,ib[key]) if key in ib else []
    layers.append(dict(layer=key[0],datatype=key[1],xor_empty=delta.is_empty(),
                       xor_polygons=delta.count(),labels_equal=la==lb,left_labels=len(la),right_labels=len(lb)))
evidence=dict(left=left,right=right,top_left=ca.name,top_right=cb.name,dbu=a.dbu,
              left_sha256=hashlib.sha256(open(left,'rb').read()).hexdigest(),
              right_sha256=hashlib.sha256(open(right,'rb').read()).hexdigest(),layers=layers,
              geometry_and_labels_identical=all(x['xor_empty'] and x['labels_equal'] for x in layers))
with open(output,'w') as f:json.dump(evidence,f,indent=2);f.write('\n')
print(json.dumps(evidence))
