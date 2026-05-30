"""techlib — the single home for DEF/SDC/tech parsing used by the extract stage.

This package consolidates the per-platform parsing logic that was historically
duplicated across the feature workers and the label extractors. Behavior is
preserved byte-for-byte (guarded by tests/test_techlib_crossplatform.py); see
``techlib.def_parse`` for the DEF/SDC parsers and the ``route_segments`` iterator.
"""
