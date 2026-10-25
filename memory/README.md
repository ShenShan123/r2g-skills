# Typed Executable Hardware Memory

The memory plane is a single package, `tehm`. It captures verified repair
transitions, derives typed views, retrieves applicable experience and binds
candidate actions to independently checked execution.

## Runtime layout

| Path | Responsibility |
| --- | --- |
| `factory.py`, `interface.py`, `contracts.py` | Process backend selection and shared data contracts |
| `tehm_backend.py`, `legacy_backend.py`, `none_backend.py` | Isolated backend adapters |
| `runtime_router.py` | R2G ingestion, recommendation and strategy routing |
| `tehm/canonical`, `tehm/evidence.py`, `tehm/verified_execution.py` | Canonical evidence and replay checks |
| `tehm/retrieval`, `tehm/activation` | Typed retrieval, binding and activation |
| `tehm/policy` | Type/applicability filters, evidence ranking, repeat control, recipes and task envelopes |
| `tehm/lifecycle`, `tehm/evolution` | Governed admission, revision and updates |
| `tehm/evaluation` | Runtime candidate/oracle checks used by admission and evolution |
| `tests` | Isolated runtime regression tests |

The former separate policy package is merged into `tehm/policy`; there is no
parallel versioned implementation or import shim. The runtime evaluation
library is retained because authority and update paths import it. Historical
experiment drivers, frozen datasets, reports, annotation tools and bundled
experience databases are not part of this source tree.

## Backend and storage

The agent defaults to `tehm`. Its store is `$R2G_HOME/memory/tehm.sqlite` and its
content-addressed artifacts are under `$R2G_HOME/memory/artifacts`. `R2G_HOME`
defaults to `~/.r2g`; `TEHM_DB` and `TEHM_ARTIFACTS_ROOT` can select another store.

`R2G_MEMORY_BACKEND=none|legacy|tehm` selects one backend per process. Direct
skill/factory calls retain `legacy` as their compatibility default. The agent
initializes an empty user-owned legacy store when requested; it does not ship
or seed historical experiment rows. TEHM never treats legacy rows as authority.

```bash
PYTHONPATH=memory python3 -m tehm.cli --help
PYTHONPATH=memory python3 -m tehm.cli --db /path/to/tehm.sqlite --artifacts /path/to/artifacts init-db
PYTHONPATH=memory python3 -m pytest -q memory/tests
```

The initialization command writes only the explicitly chosen store. Ordinary
runs without repair iterations create no artificial repair transition.

## Policy and verification

Policy configuration uses `R2G_TEHM_POLICY`. `R2G_TEHM_V2_POLICY` and the old
policy-file schema identity remain accepted for configuration compatibility;
the implementation and new diagnostic field are `tehm.policy` / `tehm_policy`.
Optional concrete recipes use `tehm.policy.recipes` through the same executor
contract. No experiment driver is needed for either runtime path.

Candidates may be tried under execution and verification gates. A candidate
trial does not promote a rule; missing evidence remains `UNKNOWN`. Read-only
evaluation stores are not initialized or migrated during agent startup.
