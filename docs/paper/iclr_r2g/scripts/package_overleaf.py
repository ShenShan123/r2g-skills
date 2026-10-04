#!/usr/bin/env python3
"""Package the current manuscript and all TeX/PDF dependencies."""
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import re
ROOT=Path(__file__).resolve().parents[1]
files=[ROOT/n for n in ['main.tex','main.bbl','references.bib','iclr2027_conference.sty','iclr2027_conference.bst','math_commands.tex','natbib.sty','fancyhdr.sty']]
files+=sorted((ROOT/'sections').glob('*.tex'))+sorted((ROOT/'tables').glob('*.tex'))
tex='\n'.join(p.read_text() for p in files if p.suffix=='.tex')
figures=set(re.findall(r'\\includegraphics(?:\[[^]]*\])?\{([^}]+)\}',tex))
files+=[ROOT/name for name in sorted(figures)]
with ZipFile(ROOT/'iclr_r2g_source.zip','w',ZIP_DEFLATED) as z:
 for p in files:z.write(p,str(p.relative_to(ROOT)))
 z.writestr('BUILD.txt','Compile main.tex with pdfLaTeX + BibTeX, or upload this ZIP to Overleaf.\nOfficial ICLR 2027 anonymous review template. Includes the completed graph-conversion retest, Full R2G repair results, and downstream stage/geometry study.\nReview AI use disclosure against actual author workflows before submission.\n')
with ZipFile(ROOT/'iclr_r2g_source.zip') as z:
 assert z.testzip() is None
 for name in re.findall(r'\\input\{([^}]+)\}',tex):
  assert (name if name.endswith('.tex') else name+'.tex') in z.namelist(),name
print(f'Packaged {len(files)} files, all input dependencies checked.')
