"""Conservative SKY130 met3 pin/PDN spacing witnesses for repair selection.

Only north-oriented rectangular pins and horizontal power/ground wire segments
are interpreted. Via geometry and unsupported orientations are not guessed.
DRC remains the authoritative verdict; this only connects its markers to a
specific, independently visible spacing mechanism.
"""
from pathlib import Path
import hashlib
import math
import re
import xml.etree.ElementTree as ET


def _intersects(a, b):
    return a[0] <= b[2] and b[0] <= a[2] and a[1] <= b[3] and b[1] <= a[3]


def diagnose(def_path, report_path):
    raw=Path(def_path).read_bytes();text=raw.decode(errors='replace')
    units=re.search(r'UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;',text)
    die=re.search(r'DIEAREA\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)',text)
    if not units or int(units[1])!=1000 or not die:return None
    bounds=tuple(map(int,die.groups()))
    pins=[];stripes=[];markers=[]
    section=re.search(r'\bPINS\s+\d+\s*;(.*?)END PINS',text,re.S)
    if section:
        for match in re.finditer(r'-\s+(\S+)\s+\+ NET\s+(\S+)(.*?);',section[1],re.S):
            name,net,body=match.groups()
            if not re.search(r'\+ USE SIGNAL\b',body):continue
            if len(re.findall(r'\+ PORT\b',body))!=1:continue
            layer=re.findall(r'\+ LAYER met3\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)',body)
            placement=re.findall(r'\+ (?:PLACED|FIXED)\s*\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)',body)
            if len(layer)!=1 or len(placement)!=1 or placement[0][2]!='N':continue
            x0,y0,x1,y1=map(int,layer[0]);px,py=map(int,placement[0][:2])
            box=(px+min(x0,x1),py+min(y0,y1),px+max(x0,x1),py+max(y0,y1))
            if box[2]-box[0]!=800 or box[3]-box[1]!=300:continue
            if min(abs(box[0]-bounds[0]),abs(box[2]-bounds[2]))>5:continue
            pins.append((name,net,box))
    section=re.search(r'\bSPECIALNETS\s+\d+\s*;(.*?)END SPECIALNETS',text,re.S)
    if section:
        for match in re.finditer(r'-\s+(\S+)(.*?);',section[1],re.S):
            net,body=match.groups()
            if not re.search(r'\+ USE (?:POWER|GROUND)\b',body):continue
            for seg in re.finditer(r'(?:ROUTED|NEW)\s+met3\s+(\d+)\s+(?:\+ SHAPE \w+\s+)?\(\s*(-?\d+)\s+(-?\d+)\s*\)\s*\(\s*(\*|-?\d+)\s+(\*|-?\d+)\s*\)',body):
                width,ax,ay=int(seg[1]),int(seg[2]),int(seg[3])
                bx=ax if seg[4]=='*' else int(seg[4]);by=ay if seg[5]=='*' else int(seg[5])
                if width<=0 or ay!=by or ax==bx:continue
                # Omit end extensions: this inner rectangle is certainly metal,
                # even when the DEF path specifies flush or rounded endpoints.
                stripes.append((net,(min(ax,bx),ay-width/2,max(ax,bx),ay+width/2)))
    report=Path(report_path).read_bytes()
    try:report_root=ET.fromstring(report)
    except ET.ParseError:return None
    for item in report_root.findall('.//items/item'):
        if (item.findtext('category') or '').strip("'\"")!='m3.2':continue
        points=[]
        for value in item.findall('.//value'):
            value=value.text or ''
            if not re.match(r'^\s*(edge-pair|edge|box|polygon)\s*:',value):continue
            points.extend((float(x)*1000,float(y)*1000) for x,y in re.findall(r'(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)',value))
        if not points:return None
        markers.append((min(x for x,y in points),min(y for x,y in points),max(x for x,y in points),max(y for x,y in points)))
    if len(markers)<2:return None
    witnesses=[];covered=set()
    for name,net,box in pins:
        for power,metal in stripes:
            if net==power:continue
            dx=max(0,box[0]-metal[2],metal[0]-box[2]);dy=max(0,box[1]-metal[3],metal[1]-box[3])
            gap=math.hypot(dx,dy)
            if gap>=300:continue
            # Match report markers locally to this pin, not to the full length
            # of a power stripe that may cross the entire design.
            nearby=(box[0]-300,box[1]-300,box[2]+300,box[3]+300)
            indices=[i for i,b in enumerate(markers) if _intersects(b,nearby)]
            if not indices:continue
            covered.update(indices)
            witnesses.append(dict(pin=name,power_net=power,layer='met3',pin_bbox_dbu=box,
                                  pdn_inner_bbox_dbu=metal,gap_upper_bound_um=round(gap/1000,6),marker_indices=indices))
    if len(covered)/len(markers)<0.8:return None
    return dict(rule='m3.2',units_per_um=1000,required_spacing_um=0.3,
                marker_count=len(markers),covered_marker_count=len(covered),witnesses=witnesses,
                def_sha256=hashlib.sha256(raw).hexdigest(),report_sha256=hashlib.sha256(report).hexdigest())
