"""Separate authorized recovery skips local interrupts, never global faults."""
import json


def active_registration(root):
    return json.loads((root / 'inputs/registration.json').read_text())


def admission_blockers(root):
    p = root / 'reports/TECHNICAL-PAUSE.json'
    return [{'global_technical_fault': str(p)}] if p.exists() else []
