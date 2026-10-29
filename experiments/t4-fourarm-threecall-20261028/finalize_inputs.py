"""Bind inherited qualification paths explicitly, without changing verdicts."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
ORIGINAL=ROOT.parent/'t4-swe20-local-20261026/qualification'


def main():
    if (ROOT/'registration.json').exists():raise ValueError('already frozen')
    for path in (ROOT/'swe/qualification').glob('*/qualification.json'):
        data=json.loads(path.read_text())
        if data['status']!='PASS':continue
        suffix='-r3' if data.get('previous_receipt')=='qualification-before-runtime-pythonpath.json' else ''
        source=ORIGINAL/path.parent.name/'qualification.json'
        data['inherited_qualification']=str(source)
        data['inherited_qualification_sha256']=hashlib.sha256(source.read_bytes()).hexdigest()
        for label,wanted in [('baseline','FAIL'),('reference','PASS')]:
            target=Path(data.get(label+'_grade',str(ORIGINAL/path.parent.name/(label+'-eval'+suffix)/'grade.json')))
            if json.loads(target.read_text())['status']!=wanted:raise ValueError('invalid original qualification')
            data[label+'_grade']=str(target)
        path.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n')


if __name__=='__main__':main()
