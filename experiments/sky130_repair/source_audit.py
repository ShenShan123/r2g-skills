"""Fail-closed evidence for the campaign's narrowly allowed source repairs.

This checks source/data preservation, not RTL-to-layout formal equivalence.
Compilation and independent DRC/LVS remain required.
"""
import hashlib
import re
from pathlib import Path


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def audit_sources(root, rtl, work, original_files, sources, frontend, include_dirs=(), restored=None, overrides=None, inactive_includes=()):
    restored, overrides = restored or {}, overrides or {}
    root, rtl, work = Path(root).resolve(), Path(rtl).resolve(), Path(work).resolve()
    selected = {Path(p).resolve() for p in sources}
    include_dirs = [rtl / Path(p).resolve().relative_to(root) for p in include_dirs]
    reached = set()
    inactive = {tuple(entry) for entry in inactive_includes}
    skipped_inactive = []
    pending = list(selected)
    while pending:
        p = pending.pop()
        if p in reached:
            continue
        if not p.is_relative_to(rtl) or not p.is_file():
            raise ValueError('unverifiable HDL source/include: ' + str(p))
        reached.add(p)
        text = frontend._strip_comments(p.read_text(errors='surrogateescape'))
        for ref in frontend._INCLUDE_RE.findall(text):
            candidates = [p.parent/ref, rtl/ref, *(d/ref for d in include_dirs)]
            found = [c.resolve() for c in candidates if c.is_file()]
            if not found:
                key = (str(p.relative_to(rtl)), ref)
                if key in inactive:
                    skipped_inactive.append(key)
                    continue
                raise ValueError('unresolved include: ' + ref)
            # Include search order differs across frontends. Ambiguous contents
            # require explicit diagnosis rather than choosing a convenient file.
            if len({digest(c) for c in found}) != 1:
                raise ValueError('ambiguous include contents: ' + ref)
            pending.append(found[0])
    removed = {rtl / Path(p).relative_to(root) for p in original_files} - selected
    if not removed <= reached:
        raise ValueError('removed source is not reachable through retained includes: ' +
                         ', '.join(str(p.relative_to(rtl)) for p in sorted(removed-reached)))
    evidence, data, deferred = [], [], []
    for copied in sorted(rtl.rglob('*')):
        if not copied.is_file():
            continue
        original = root / copied.relative_to(rtl)
        if not original.is_file() or not original.resolve().is_relative_to(root):
            entry=restored.get(str(copied.relative_to(rtl)))
            if entry and copied.suffix.lower() in ('.hex','.mem','.bin') and digest(copied)==entry['sha256']:
                evidence.append(dict(path=str(copied.relative_to(rtl)),upstream=entry,private_sha256=digest(copied)))
                continue
            raise ValueError('private source has no original within repository: ' + str(copied))
        before, after = digest(original), digest(copied)
        if copied.suffix.lower() in ('.v', '.sv', '.vh', '.svh'):
            text = original.read_bytes().decode(errors='surrogateescape')
            expected = text
            for ref, rel in overrides.items():
                entry=restored.get(rel);private=rtl/rel
                if not entry or not private.is_file() or digest(private)!=entry['sha256']:
                    raise ValueError('unverified upstream memory reference: '+ref)
                expected=expected.replace('"'+ref+'"','"/work/'+str(private.relative_to(work))+'"')
            for ref, how in dict.fromkeys(frontend.readmem_references(text)):
                if ref in overrides:
                    data.append(dict(consumer=str(copied.relative_to(rtl)),reference=ref,
                                     resolution='verified_original_upstream_blob',path=overrides[ref],
                                     sha256=restored[overrides[ref]]['sha256']))
                    continue
                if not ref and how.startswith('parameter '):
                    deferred.append(dict(consumer=str(copied.relative_to(rtl)),parameter=how,
                                         reason='empty default; actual elaborated override must be checked by synthesis'))
                    continue
                found, via = frontend._resolve_data(ref, original, root)
                if not found or not found.resolve().is_relative_to(root):
                    # Unrelated files copied for includes may have unresolved
                    # memories; only reachable design sources matter here.
                    if copied in reached:
                        raise ValueError('unresolved or external memory data: ' + ref)
                    continue
                found = found.resolve()
                private = rtl / found.relative_to(root)
                if not private.is_file() or digest(private) != digest(found):
                    raise ValueError('initialization data differs from original: ' + ref)
                data.append(dict(consumer=str(copied.relative_to(rtl)), reference=ref,
                                 resolution=via, path=str(found), sha256=digest(found)))
                # Existing absolute paths are left untouched by fix_sources.
                if not Path(ref).is_absolute():
                    expected = expected.replace('"'+ref+'"', '"/work/'+str(private.relative_to(work))+'"')
            if before != after and copied.read_bytes() != expected.encode(errors='surrogateescape'):
                raise ValueError('unapproved RTL content change: ' + str(copied.relative_to(rtl)))
        elif before != after:
            raise ValueError('non-HDL source/data changed: ' + str(copied.relative_to(rtl)))
        evidence.append(dict(path=str(original), original_sha256=before, private_sha256=after))
    return dict(status='source_preservation_checked', formal_equivalence=False,
                original_root=str(root), private_root=str(rtl),
                removed_sources_retained_by_include=[str(p.relative_to(rtl)) for p in sorted(removed)],
                files=evidence, initialization_data=data, deferred_readmem_parameters=deferred,
                compiler_verified_inactive_includes=sorted(set(skipped_inactive)),
                upstream_reference_overrides=overrides)
