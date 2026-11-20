"""Generate an isolated, conservative access abstract without changing GDS."""
from pathlib import Path
import re, json, hashlib
import pya

root=Path(__file__).resolve().parent
src=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/lef/asap7sc7p5t_28_R_1x_220121a.lef')
gds=Path('/home/yangao/r2g_toolchain/OpenROAD-flow-scripts/flow/platforms/asap7/gds/asap7sc7p5t_28_R_220121a.gds')
l=pya.Layout();l.read(str(gds));li=l.layer(19,0)
changes=[];geometry=[]

def fmt(x):return format(x,'.6f').rstrip('0').rstrip('.') or '0'
def rect_line(coords):return '        RECT '+' '.join(fmt(x) for x in coords)+' ;'

def macro_sub(match):
    name,body=match.groups();removed=[];cell=l.cell(name)
    m1=pya.Region(cell.begin_shapes_rec(li)).merged() if cell else None
    def pin_sub(pm):
        pin,pbody=pm.groups()
        if pin in ['VDD','VSS']:return pm.group(0)
        # All standard-cell signal PORT shapes in this pinned RVT library are M1.
        # Leave any future/non-M1 pin unchanged.
        layers=re.findall(r'\bLAYER (\S+) ;',pbody)
        if set(layers)!={'M1'}:return pm.group(0)
        rects=[tuple(map(float,m)) for m in re.findall(r'RECT (\S+) (\S+) (\S+) (\S+) ;',pbody)]
        trimmed=[];loss=[]
        for x1,y1,x2,y2 in rects:
            lo=max(y1,.054);hi=min(y2,.216)
            if hi>lo:trimmed.append((x1,lo,x2,hi))
            if y1<min(y2,.054):loss.append((x1,y1,x2,min(y2,.054)))
            if y2>max(y1,.216):loss.append((x1,max(y1,.216),x2,y2))
        if not trimmed:return pm.group(0)
        if m1 is not None:
            region=pya.Region()
            for r in rects:region.insert(pya.DBox(*r).to_itype(l.dbu))
            outside=(region-m1).area()*l.dbu*l.dbu
            if outside:geometry.append({'cell':name,'pin':pin,'lef_outside_actual_m1_um2':outside})
        if not loss:return pm.group(0)
        # Replace only M1 PORT rectangle records, preserving all non-geometry text.
        iterator=iter(trimmed)
        start=pbody.index('        RECT ')
        end=pbody.rfind(' ;')+2
        # End targets the final rectangle: PORT ends use END, without a semicolon.
        newbody=pbody[:start]+'\n'.join(rect_line(r) for r in trimmed)+pbody[end:]
        removed.extend(loss)
        changes.append({'cell':name,'pin':pin,'original_rects':rects,'access_rects':trimmed,'blocked_rects':loss})
        return '  PIN '+pin+'\n'+newbody+'  END '+pin
    newbody=re.sub(r'  PIN (\S+)\n(.*?)  END \1',pin_sub,body,flags=re.S)
    if removed:
        geom='    LAYER M1 ;\n'+'\n'.join(rect_line(r) for r in removed)+'\n'
        obs=re.search(r'(?m)^\s*OBS\s*$',newbody)
        if obs:newbody=newbody[:obs.end()]+'\n'+geom+newbody[obs.end():]
        else:newbody+='\n  OBS\n'+geom+'  END\n'
    return 'MACRO '+name+'\n'+newbody+'\nEND '+name

result=re.sub(r'MACRO (\S+)\n(.*?)\nEND \1',macro_sub,src.read_text(),flags=re.S)
dst=root/'asap7sc7p5t_R_pin_window_candidate.lef';dst.write_text(result)
manifest={'source':str(src),'source_sha256':hashlib.sha256(src.read_bytes()).hexdigest(),
 'gds':str(gds),'gds_modified':False,'access_window_um':[.054,.216],
 'changed_pins':len(changes),'changed_cells':len(set(c['cell'] for c in changes)),
 'original_pin_shapes_outside_actual_m1':geometry,'changes':changes,
 'status':'Conservative pin-access abstract; removed pin metal retained as OBS. Flow/signoff pending.'}
(root/'pin_window_provenance.json').write_text(json.dumps(manifest,indent=2)+'\n')
print(json.dumps({k:v for k,v in manifest.items() if k not in ['changes']},indent=2))
