import hashlib
import importlib.util
from pathlib import Path
import xml.etree.ElementTree as ET

import pytest


PATH = Path('/home/yangao/r2g_nangate45_baseline_20260917/runtime/r2g-skills/signoff-loop/scripts/flow/_n45_contact_deck.py')
spec = importlib.util.spec_from_file_location('n45_campaign_deck', PATH)
deck = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deck)


def test_unvalidated_deck_is_rejected():
    with pytest.raises(ValueError, match='Unvalidated'):
        deck.transform(b'<klayout-macro><text>deep</text></klayout-macro>')


def test_only_mode_changes_and_manifest_is_written(monkeypatch, tmp_path):
    expr = 'cont.not(active.or(poly.or(metal1)))'
    ruby = 'FEOL=true\nBEOL=true\nOFFGRID=true\nANTENNA=true\ndeep\nthreads(4)\n' + expr + '.output("CONTACT.3")\n'
    data = ('<klayout-macro><text>' + ruby + '</text></klayout-macro>').encode()
    monkeypatch.setattr(deck, 'SOURCE_SHA256', hashlib.sha256(data).hexdigest())
    source, target = tmp_path/'original', tmp_path/'flat.lydrc'
    source.write_bytes(data)
    deck.generate(source, target)
    assert source.read_bytes() == data
    assert ET.fromstring(target.read_bytes()).findtext('text') == ruby.replace(expr, 'cont.not(active).not(poly).not(metal1)')
    assert target.with_suffix('.mode.json').is_file()


def test_missing_mode_is_rejected(monkeypatch):
    data = b'<klayout-macro><text>flat</text></klayout-macro>'
    monkeypatch.setattr(deck, 'SOURCE_SHA256', hashlib.sha256(data).hexdigest())
    with pytest.raises(ValueError, match='CONTACT'):
        deck.transform(data)
