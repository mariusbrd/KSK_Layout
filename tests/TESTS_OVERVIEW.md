# Tests Overview

Purpose: main app-facing tests.

Read when:

- changing code in `KSK_Layout/`
- looking for product-near regression checks

Ignore when:

- task is only root `kpi_reference.py`
- task is only manual ATZ/user repro

Typical use:

- start with `test_*`
- use `check_*` for focused checks
- use `repro_*`, `debug_*`, `trace_*` only for bug hunting
- `fixtures/` holds golden-master/reference data

Order:

- first test surface; see [`../../_workspace_meta/TEST_NAVIGATION.md`](../../_workspace_meta/TEST_NAVIGATION.md)
