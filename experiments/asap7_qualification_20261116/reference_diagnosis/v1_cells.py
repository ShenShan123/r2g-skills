import pya
ly=pya.Layout(); ly.read(GDS)
li=[l for l in ly.layer_indexes() if ly.get_info(l).layer==21]
out=[]
for c in ly.each_cell():
    n=sum(c.shapes(l).size() for l in li)
    if n: out.append((c.name,n))
print(len(out),"cells with own V1 shapes:"); print(sorted(out))
