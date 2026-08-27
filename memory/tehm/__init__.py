"""Typed Executable Hardware Memory (TEHM).

A complete replacement memory backend for the R2G EDA agent (design doc:
``memory/docs/Typed_Executable_Hardware_Memory_R2G.md``). The memory atom is a
**Verified State Transition**; episodes are **Repair Episode Graphs**; reusable
knowledge is **Crystallized Procedural Rules**; the global organization is a
**Versioned Verified Experience Graph**.

This package implements the Phase 2-3 foundation: the canonical store
(states / transitions / episodes) and the five materialized views (semantic /
diagnostic / episodic / procedural / parametric). It is deliberately isolated
from the legacy ``signoff-loop/knowledge`` memory plane — see ``honesty.H5``.

Package layout mirrors design doc 18.2:
    tehm/
      config.py          env knobs + paths
      schema.sql         tehm_* tables (design doc 19.2-19.7)
      db.py              connect / ensure_schema / migrations
      ids.py             content-addressed id minting
      artifact_store.py  content-addressed artifact store
      canonical/         verified state / transition / episode / verifier
      graph/             LocalDesignGraph + roles + predicates
      views/             five typed views + materialization
      honesty.py         TEHM honesty gates
      cli.py             subcommand CLI
"""
from __future__ import annotations

__version__ = "0.1.0"
# Keep this explicit and reviewable: importing config here would create a
# package-initialisation cycle because config is imported by tehm.db.
SCHEMA_VERSION = "tehm-v4"
PREDICATE_SCHEMA_VERSION = "predicate-v0.1"
ROLE_SCHEMA_VERSION = "role-v0.1"
