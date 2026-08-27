"""Memory backend seam (design doc 17.1-17.4).

``R2G_MEMORY_BACKEND=none|legacy|tehm`` selects the memory plane at process
start (locked, fail-closed). This package hosts the unified ``MemoryBackend``
interface, the shared contracts, the factory, and the three backend adapters:
``none`` / ``legacy`` / ``tehm``.

Layout (mirrors design doc 17.1):
    memory/
      interface.py       MemoryBackend Protocol
      contracts.py       shared data contracts (ExecutionRecord, RepairContext, ...)
      factory.py         open_memory_backend(name) — reads R2G_MEMORY_BACKEND
      none_backend.py    no-memory baseline
      legacy_backend.py  wraps the legacy signoff-loop/knowledge plane
      tehm_backend.py    wraps the TEHM canonical store
      tehm/              the TEHM implementation package (canonical/graph/views/...)
"""
