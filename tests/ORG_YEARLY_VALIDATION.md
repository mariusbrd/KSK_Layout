# Annual OE/cluster aggregation: step 1 reference

Before extending result storage or aggregation, seven synthetic full-run references
were captured on branch `Exportfunktion-OrgSimulation`. No production code was changed.

## Reference scenarios

- Zero horizon (identity).
- Two-year horizon without events.
- Retirement and quits.
- External hires.
- Existing ATZ contract end and dormant employment start/return.
- New apprentices and internal conversion out/in.
- Combined departures, hires, apprentices, ATZ and dormant employment.

All scenarios use fixed dates (2025-12-31 to 2027-12-31, except identity), seed 42,
synthetic people and an explicit two-cluster mapping. User settings are isolated
in memory with `load_user_settings` patched to return an empty dictionary. Actual
simulation/forecast and finalization functions run without mocks. These tests do
not read private employee source files or write user settings.

The frozen JSON contains complete final snapshots, final employee frames,
departure/accession events and forecast KPIs, including columns, index and dtypes.
The engine SHA-256 at capture is recorded for provenance. Comparison covers all
serialized values; JSON float precision is 15 decimal places, so this is not a
bitwise floating-point comparison. The input frame is also checked for mutation
using `assert_frame_equal(check_exact=True)`.

Run: `py -m pytest tests/test_org_yearly_reference.py -q --disable-warnings`

Normal test execution never refreshes expected results. Explicit capture refuses
to overwrite an existing reference. Future changes must be checked against this
reference, rather than silently accepting new expected output.

## Step 1 validation

- Seven frozen-reference comparisons passed.
- 26 existing compact simulation, cluster-context and OE page tests passed.
- `git diff --check` passed.

This captures existing behavior, including any existing defects; it does not
prove future event aggregation reconciles to final reporting MAK. Dedicated
aggregation tests will follow. ATZ phase transition and apprentice non-retention
need separate cases when validating event classification. No annual intermediate
population snapshots or additional production simulation calls are introduced.

Next acceptance step: extend result storage on page 7 with raw events and the
mapping from the same run; keep the simulation engine unchanged.

## Step 2: additional result storage

Page 7 now stores `compact_sim_event_context` with the run signature, copied
metadata, full raw departure and accession event frames, and the actual cluster
mapping bundle. The context is prepared after the single simulation call and
saved alongside the existing results. The existing cache reset removes it.
Empty outputs remain empty; no fallback to current settings or cluster files is
introduced. Existing sessions acquire this context on the next explicit computation.

Ten new storage checks cover all seven real reference scenarios, independence
from later edits, empty results and cache reset. The existing page integration
test now verifies stored events, matching run signature and reuse on a rerun
without another simulation call; its result stub was extended to the full result
contract. The reviewed lineage hash for page 7 `main` was updated explicitly.

Validation: 48 tests passed across reference, storage, engine, cluster-context,
OE page and code-lineage tests. `git diff --check` passed. The compact simulation
engine and both forecast engines have no changes. No UI or aggregation has been
added in this step. Next acceptance step: classify and aggregate these stored
events by year and OE/cluster, without invoking any simulation function.

Additional file verification: the locally resolved persisted source
`config/cluster_mapping.xlsx` was read directly (OrgUnits sheet) and compared
with both the existing loader bundle and the new copied context. All 46 OE
mappings agreed; one distinct cluster value and no duplicate OE rows were found.
The source signature also agreed. This checks the locally persisted source, not
a possibly different upload held only in a running browser session. A separate
temporary Excel-file test covers three OEs in two user-defined clusters and
verifies that replacing the source file does not alter the saved mapping.

## Step 3: pure event normalization and aggregation

`utils/org_simulation_events.py` operates exclusively on a supplied saved event
context. It does not import or call any simulation, source loader or settings
function. Every event retains its date and signed headcount/MAK delta. OE clusters
are assigned from the saved mapping; missing assignments remain visible.

Categories distinguish company departures (including apprentice non-retention),
external entries, internal apprentice conversion and capacity-only changes.
Unknown event types are kept as other changes rather than guessed to be leavers.
Missing dates, nonfinite numeric changes, incomplete event schemas and mismatched
run signatures cause explicit errors rather than silent loss of event rows.

Aggregation sums by calendar year, OE or OE cluster, category and event type.
The same function can produce full-horizon totals. Values are event changes,
not reconstructed year-end populations or finalized reporting MAK stocks.

18 new checks cover category semantics, specific cluster/year/type totals,
unassigned OEs, calendar boundaries, preservation of repeated person events,
unknown types, invalid data, input immutability and all seven real reference
scenarios. Combined verification: 67 tests passed including storage, references,
engine, cluster-context, OE page and code lineage. `git diff --check` passed.

The module is deliberately not wired into storage, UI or export yet. Next
acceptance step: integrate aggregation with the saved result context, using its
run signature, then add the annual-change display and export in later steps.

## Step 4: integration into result storage

Page 7 builds `compact_sim_event_summary` from the context after its existing
single simulation call, before publishing the new result to session state. It
contains copied run metadata, normalized events and four tables: annual OE,
annual OE cluster, full-horizon OE and full-horizon OE cluster. The builder
checks the expected run signature. Cache clearing removes raw context and
summary together. No additional forecast, file load or settings lookup is used
by aggregation.

The page integration test verifies that a rerun without computation reuses both
objects with no further simulation call. An explicitly requested subsequent
computation with a different mapping replaces context, summary and run signature
together, still with exactly one simulation call per computation. Empty runs,
metadata isolation and consistent sums across all four tables are also checked.

Validation: 69 tests passed across annual events, storage, frozen references,
engine, cluster context, OE page and lineage. `git diff --check` passed. The
reviewed page 7 main lineage hash was updated; forecast engines remain unchanged.
Older cached sessions are not silently recomputed or filled from current drafts;
the new summary is available after the next explicit computation.

No new display or export has been added in this step. Next acceptance step:
render annual changes on the OE simulation page from the saved summary, with
explicit run matching and partial-year labels.

## Step 5: annual changes display

The OE simulation wrapper renders an additional section after the existing
analysis. `components/org_simulation_yearly.py` reads only the saved summary
whose run signature matches the displayed result. It offers OE/cluster level,
departures/entries/signed headcount/signed MAK change, year, organization and
event type selection, with a bar chart, year matrix and event detail table.
Event type codes have readable labels. Missing or mismatched summaries request
an explicit new computation; they never trigger a forecast automatically.

Calendar years are taken from saved run metadata, so event-free years appear as
zero. Partial first/last years show the actual dates. Selection is explicitly
local to this section and independent of stock-view filters; it never joins
events to the future population, which could discard departed people. These
values are described as event changes, not annual population or reporting MAK
stock differences. No global-filter semantics have been changed.

Nine new view tests cover partial years, zero years, dimensions, event selection,
immutability, invalid/missing run context, rendering and a real Streamlit AppTest
switching organization level and metric. The reviewed page 10 main lineage hash
was updated. Export integration remains the next acceptance step.

## Step 6: comprehensive export

The annual section now renders immediately before the existing full-export
control, allowing the exact selected view to be passed to that export. The shared
OE renderer only enables this path when simulation export context is supplied.
The ordinary IST analysis remains unchanged.

The workbook adds the selected year matrix (with its organization index made an
explicit column), chart rows and readable event details, plus separate full-run
annual and overall OE/cluster tables, dated year boundaries and the canonical
event basis. Run metadata and selection (including event types) are documented.
Cluster assignment rows and, when captured in the summary, the full source mapping
and its signature are included. Older summaries lacking the full mapping explicitly
document that limitation rather than reading a current file. Mismatched run
summaries are omitted and documented, never mixed into the workbook.

Lineage 10-09 documents annual event classification, aggregation, selected view
and export with four new code references. Reviewed hashes for the shared OE
renderer, simulation wrapper and workbook builder were updated explicitly.
The standard validation runner includes all new annual/reference test files.

Validation in the actual dashboard environment (Conda general, Streamlit 1.47.1):
107 combined export, annual view/event, storage, reference, engine, cluster,
OE page and lineage tests passed. After adding checks for real period-valued
event columns and empty runs, all seven annual-export tests passed separately.
The UI/export integration test changes year, cluster and event selection and
verifies workbook cache invalidation while full-run sums remain unchanged.
Simulation engines remain unchanged; no additional production simulation call
or source-file load is introduced.

The transformation trace additionally explains saved-event classification,
annual aggregation, current selection and export as four explicit steps.
All 25 focused transformation, lineage export, page coverage and code-hash checks
passed after adding that trace. `git diff --check` passed. No changes were made
to user input files, the user-uploaded mapping or forecast-engine code.

## Stock reconciliation: first acceptance step

Page 7 additionally freezes prepared initial/final snapshots and accession
parameters in the same event context. Normalization fills missing departure OEs
from the initial snapshot by normalized person ID. Existing event OEs remain
unchanged. Repeated position rows in one OE are unambiguous; multiple distinct
OEs remain explicitly ambiguous. Unknown people and vacant rows do not produce
guessed assignments. Raw forecast outputs are never mutated.

The previously demonstrated ATZ_END reference discrepancy is resolved: initial
headcount/MAK plus completed event changes equals final headcount/MAK for OE1 and
OE2 without any balancing adjustment. This is a focused reference result, not
yet a general proof for multiple-OE allocation or all MAK finalization effects.

Validation in the dashboard environment: 51 event/storage/reference/cluster tests,
29 lineage/yearly UI/export tests, and all five focused completion tests passed.
The normalizer and page 7 reviewed hashes plus the new completion reference were
recorded. The simulation engine and both forecasts remain unchanged.

An explicit new computation is required to populate stock bases in old sessions.
Next acceptance step: restrict stock projection to accession-disabled runs,
build the annual bridge and compare its final values at company/OE/cluster level.
No stock-projection UI or generalized end-value certification is present yet.

## Stock reconciliation: arithmetic bridge and end checks

`build_departure_stock_bridge` now derives initial and annual stock values from
the stored initial snapshot and cumulative signed departure events. The summary
contains this result under `stock_reconciliation`, with annual rows, end-value
checks and a status. Company, OE and cluster stocks are calculated independently
using existing headcount/MAK functions; heads are not summed across OEs because
people with multiple OE positions can occur in more than one local headcount.

Eligibility requires explicit false active flags for apprentice, trainee and
regular accession blocks. Actual accession events, missing inputs, unknown event
types or events outside the run horizon prevent certification. Missing/ambiguous
OE events, negative projected stocks or end differences prevent an agreed status.
No endpoint replacement, residual distribution or additional simulation occurs.

Headcount agreement is exact. Existing stock MAK functions round to two decimal
places; comparison requires the same rounded result AND an unrounded difference
no larger than 0.005 MAK (half the stock display precision). The arithmetic values
and actual differences remain visible in the data. This is explicitly rounded
reporting equivalence, not bitwise equality of raw capacity values.

Nineteen focused reconciliation checks passed, including actual ATZ AR-to-FR
events, no-event and retirement/quit cases, dormant employment, identity on a
finalized input, mixed/active accession rejection, ambiguous multiple-OE cases,
end differences, dates, unknown types and rounding thresholds. The identity test
uses an already-finalized input, as the real zero-horizon engine contract requires.
The new bridge code reference is recorded in the lineage baseline.

The result is stored but is not displayed or exported yet. Next acceptance step:
add stock-view controls and reconciliation evidence to UI/export, displaying
unavailable or discrepant runs explicitly instead of a certified stock chart.

Combined final verification: all 95 reconciliation, event, storage, reference,
cluster, yearly UI/export and lineage tests passed in the dashboard environment.
`git diff --check` passed; compact and departure/accession engines have no changes.

## Stock reconciliation: display and export

`components/org_simulation_stock.py` renders the saved bridge before the annual
event section. Matching signatures and an agreed bridge status are required for
the line chart and matrix. Active-accession/missing-input cases show information;
discrepant cases show warning and checks only. Controls switch company/OE/cluster
level, heads/MAK and organizations independently of event/stock filters. MAK chart
values are rounded to the same two decimal places as stock KPIs; full arithmetic
rows and differences remain available in the reconciliation detail expander.
Coincident initial/year-end dates are deduplicated for plotting only.

The comprehensive workbook adds full bridge rows, end-value checks and selected
chart/matrix data. Discrepant runs export checks and status without a certified
stock curve. Missing or mismatched contexts are never exported as stock data.
Lineage 10-10 documents the eligibility, arithmetic, precision and output rules.
Two new UI/export code references and the two reviewed existing renderer/workbook
hashes are recorded; the validation runner includes the stock test file.

Seven focused UI/export tests passed in Streamlit 1.47.1, covering level/metric
switches, exact selected data, real Excel roundtrip, mismatch, active accessions
and discrepancies. No simulation call is made by display or export.

## Unified annual changes and stock view

The separate stock section has been integrated into `Veränderungen pro Jahr`
via a changes/remaining-stock selector. Year and OE/cluster selection are shared;
stock mode includes organizations without events and works for event-free runs.
The stock starting point always remains visible alongside selected year-end
dates. Every selected annual slice is represented in a categorical dated bar
chart and matrix, rather than relying on automatically sampled time-axis ticks.
Missing annual rows in an older cached bridge request explicit recomputation;
there is no interpolation. Event-type selection does not restrict stock projection.

The selected stock years are documented in the existing full export. A real
Streamlit integration test checks all annual slices on OE and cluster levels,
switching heads/MAK, selecting a subset of years and exact export data reuse.
Three reviewed display/export hashes were updated. Simulation and stock-bridge
arithmetic remain unchanged.

## Correcting reporting allocation and raw precision

The supplied CSV has a one-head and -1.4935 MAK total discrepancy, 40 failing
OE rows and five failing cluster rows. The existing Downloads workbook (3)
matches its final total (345 heads, 295.90 MAK), but matching totals alone do not
prove identical run provenance. That workbook has 24 original multi-OE people
and 12 surviving people with changed OE assignments. Its canonical event sheet
lacks person IDs, so not every discrepancy can be attributed to a dated person
event from the old export. Current persisted input files are not substituted for
historical run data.

Corrections now use raw reporting capacity for initial/final comparisons, with
1e-6 numerical tolerance and presentation rounding only. Scope event effects use
each original person's actual group memberships: one head per affected OE or
cluster and capacity according to the saved row distribution. Company headcount
remains unique and is not derived by summing overlapping groups. Raw forecast
events remain unchanged.

Per-person diagnostics show original/final OEs, initial/final capacities, event
deltas and unexplained residuals. They do not turn residuals into dated correction
events. An undocumented OE reassignment or non-event finalization effect remains
uncertified until its causal timing is established. These corrections therefore
do not yet guarantee that every case in the supplied live run is resolved.

UI/export expose diagnostics, raw initial/final audit fields and person IDs in
the event basis. The simulation wrapper refreshes versioned summaries from the
same stored context once without another forecast or input-file read. Four new
scope/capacity/diagnostic references and five reviewed changed references are
recorded in lineage (91 total). The trace describes raw rather than rounded
comparison.

Validation: 147 combined tests passed before the final precision/schema guards;
all 39 targeted reconciliation, stock and code-lineage tests passed afterwards.
New checks cover fractional raw values, multi-OE exits within/across clusters,
unexplained capacity/headcount effects, undated OE reassignment and cached summary
refresh. Simulation and both forecast engines remain unchanged. No original data
or user settings were modified. Remaining live-run diagnosis requires the newly
enriched export from that saved run, not a recomputed or substituted scenario.

Follow-up CSV (16-14): sixteen former failures are resolved; total remaining
difference is exactly +1 head and -1.5 raw MAK. A false cluster failure with a
1.4e-14 raw difference was caused by comparing independently rounded values at
the 35.605 boundary. The redundant rounded-equality condition was removed; the
1e-6 raw tolerance remains unchanged. Cached summary version 3 refreshes the
check from the same context without a forecast. The supplied CSV again contains
only aggregate checks, not the 15 per-person diagnosis rows. Thus timing/causes
of remaining genuine finalization discrepancies are not inferred from it.

## Complete reconciliation of supplied workbook (4)

The full saved-run workbook identifies all remaining effects: twelve surviving
people undergo deterministic OE consolidation, one also loses 0.3077 reporting
MAK through the existing zero-hours/position allocation rule. Two people already
have zero reporting MAK in ATZ freistellung at baseline; their original forecasts
nevertheless emit -1 MAK at a later AR-to-FR event. No new retirement behavior,
probability or simulation decision was introduced.

The postprocessor now reuses `_update_existing_rows`, capacity calculation,
explicit saved cluster mapping, saved exclusion settings and person allocation
to construct a reporting projection basis. This is a technical entry into the
forecast view, not a dated real-world OE transfer. Its effects are exposed
separately and applied once at the first future annual observation. The original
IST observation is preserved. Actual event reporting effects use the saved
capacity budget/masks: already-zero capacity is not lost twice, dormant return
uses the saved reporting source, and exits remove the remaining capacity.
Original forecast rows/deltas remain unchanged and are exported alongside the
reporting effects. No forecast is run and no endpoint residual is inserted.

Validation against workbook (4) reproduces 397 heads and 336.6155 raw MAK, with
all company/OE/cluster checks agreed and no remaining person diagnostics.
Reconstruction of this older Excel export repaired 58 dates rounded into the
next year using the separately stored original Jahr, solely in the in-memory
audit reader. This does not modify the workbook. New exports preserve subsecond
timestamps as ISO strings, tested at 2036-12-31 23:59:59.999999999.

153 combined regression tests passed. Additional focused tests cover saved-rule
multi-OE consolidation, the partial-capacity/exclusion case, already-freistellung
ATZ, dormant return and precise Excel timestamps. Code lineage has 93 references.
Version 4 refreshes summaries from the saved context/settings on opening the OE
simulation page, without another simulation. Simulation and forecast engines
remain unchanged. Technical effects and changed event reporting are visible in
the detail expander and comprehensive export.
# Separater Export der aktuellen Jahresdarstellung

Erweiterung: Pro ausgewählter Abgangsart ein eigenes jährliches Matrixblatt,
mit allen ausgewählten OEs/Clustern einschließlich Nullzeilen. Kennzahl und Jahre
werden aus der sichtbaren Ansicht übernommen. Im Bestandsmodus gibt es eine
eigene Export-Artauswahl; die Veränderungsansicht verwendet ihre Ereignisfilter.
Die Originalereignisse werden mit der vorhandenen build_yearly_view-Funktion
aggregiert. Dokumentation und Verzeichnis benennen Art, Kennzahl und Vorzeichen
sowie die Abgrenzung gegenüber technischen Bestands- und Reportingeffekten.

Der kleine Excel-Button neben der Kennzahl exportiert die sichtbare Auswahl für
Bestandsverlauf und Ereignisveränderungen. Diagrammdaten und Matrix stehen zuerst;
die weiteren Blätter enthalten ausdrücklich den vollständigen gespeicherten Lauf
als Nachweis, einschließlich Clusterzuordnung und Lineage. Bestandsnachweise
verwenden die bestehende Überleitung. Keine erneute Simulation oder Änderung
der Originalereignisse. Gespeicherte Parameter werden nur bei gleicher Laufkennung
übernommen. Der bestehende Export-Fingerprint berücksichtigt alle Werte und Filter.

Validiert: 43 bestehende Tests für Bestandsanzeige, Jahresansicht, Exporte,
Reportingeffekte und Lineage sowie 3 neue Auswahl-Exporttests erfolgreich.
Die neuen Tests prüfen OE/Köpfe und Cluster/MAK gegen Excel, Heritage-Blätter,
Ereignisfilter, Laufkennung, unveränderte Daten und Cache-Invalidierung.

