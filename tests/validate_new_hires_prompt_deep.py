import sys
import traceback
from collections import defaultdict

import pandas as pd

sys.path.insert(0, 'KSK_Layout')
from zugaenge.forecast import run_forecast_zugaenge


def make_snap(units=None):
    if units is None:
        units = ['OE1']
    rows = []
    for i, u in enumerate(units):
        rows.append({
            'PersNr': f'E{i+1:03d}',
            'Organisationseinheit': u,
            'Jobfamily': 'Angestellte',
            'active': True,
            'mak': 1.0,
            'TrfGr': 'E9A',
            'Eintritt': pd.Timestamp('2015-01-01'),
            'OE-Cluster': 'BaseCluster',
            'JF-Cluster': 'BaseJFCluster',
        })
    return pd.DataFrame(rows)


def make_params(nh=None, seed=42):
    p = {
        'azubi': {'active': False, 'new_cases_per_year': 0, 'duration_years': 3,
                  'retention_rate': 1.0, 'strategy': 'Random', 'entry_tariff_group': 'E5',
                  'entry_step': 1, 'exclude_baseline_azubis': False,
                  'azubi_mak_during_training': 0.0, 'azubi_mak_after_takeover': 1.0,
                  'azubi_conversion_month': 8, 'azubi_conversion_day': 1,
                  'graduation_mode': 'nearest_cycle', 'nearest_cycle_grace_days': None,
                  'use_takeover_matrix': False, 'takeover_matrix': {},
                  'takeover_dimension': 'JobFamily', 'jf_to_cluster_map': {}},
        'trainee': {'active': False, 'new_cases_per_year': 0, 'duration_years': 1.5,
                    'salary_group': 'E13', 'strategy': 'Random'},
        'new_hires': {'active': True, 'count_per_year': 10,
                      'strategy': 'Random', 'target_org_unit': None,
                      'distribution': []},
        'random_seed': seed,
    }
    if nh:
        p['new_hires'].update(nh)
    return p


def run(snap, params, start='2026-01-01', end='2027-12-31', vacancies=None):
    result = run_forecast_zugaenge(
        df_snapshot=snap,
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        params=params,
        vacancies=vacancies or [],
    )
    ev = result.get('events', pd.DataFrame())
    nh = ev[ev['type'] == 'New_Hire'] if not ev.empty else pd.DataFrame()
    return ev, nh


results = []
critical = []

def run_case(block, name, fn):
    try:
        fn()
        results.append((block, name, 'PASS', ''))
    except AssertionError as e:
        results.append((block, name, 'FAIL', f'{e}\n{traceback.format_exc()}'))
    except Exception as e:
        results.append((block, name, 'ERROR', f'{e}\n{traceback.format_exc()}'))


def run_critical(name, fn):
    try:
        fn()
        critical.append((name, 'PASS', ''))
    except AssertionError as e:
        critical.append((name, 'FAIL', f'{e}\n{traceback.format_exc()}'))
    except Exception as e:
        critical.append((name, 'ERROR', f'{e}\n{traceback.format_exc()}'))


# A

def A1():
    required = ['date', 'type', 'count', 'persnr', 'org_unit', 'source', 'mak', 'TrfGr', 'Jobfamily', 'Planstelle', 'OE-Cluster']
    _, nh = run(make_snap(), make_params({'count_per_year': 10}))
    assert not nh.empty, 'No New_Hire events generated'
    for f in required:
        assert f in nh.columns, f'Feld fehlt: {f}'

def A2():
    _, nh = run(make_snap(), make_params({'count_per_year': 10}))
    assert (nh['type'] == 'New_Hire').all()
    assert (nh['count'] == 1).all()
    assert (nh['mak'] == 1.0).all()
    assert (nh['source'] == 'NewHire').all()
    assert (nh['TrfGr'] == 'E9A').all()
    assert (nh['St'] == 3).all()

def A3():
    ev_long, _ = run(make_snap(), make_params({'count_per_year': 10}), start='2026-01-01', end='2031-12-31')
    lifecycle_types = ['New_Hire_Exit', 'New_Hire_Conversion']
    assert not ev_long['type'].isin(lifecycle_types).any()

# B

def B1():
    _, nh = run(make_snap(), make_params({'count_per_year': 12}), start='2026-01-01', end='2027-12-31')
    assert abs(len(nh) - 24) <= 1, f'Erwartet ~24, erhalten {len(nh)}'

def B2():
    _, nh = run(make_snap(), make_params({'count_per_year': 0}))
    assert len(nh) == 0

def B3():
    _, nh = run(make_snap(['OE1','OE2','OE3']), make_params({'count_per_year': 100}), start='2026-01-01', end='2027-12-31')
    assert abs(len(nh) - 200) <= 2, f'Erwartet ~200, erhalten {len(nh)}'

# C

def C1():
    snap = make_snap(['OE1', 'OE2', 'OE3'])
    _, nh = run(snap, make_params({'count_per_year': 30, 'strategy': 'Random'}))
    known = snap['Organisationseinheit'].unique().tolist()
    unknown = [u for u in nh['org_unit'].unique() if u not in known]
    assert not unknown, f'Unbekannte Units: {unknown}'

def C2():
    _, nh = run(make_snap(['OE1','OE2','OE3']), make_params({'count_per_year': 20, 'strategy': 'OrgUnit', 'target_org_unit': 'OE2'}))
    assert (nh['org_unit'] == 'OE2').all(), f"Units: {nh['org_unit'].unique()}"

# D

def D1():
    vacancies = [{'date': pd.Timestamp('2026-02-01'), 'org_unit': 'OE2', 'planstelle': 'Kreditanalyst', 'Jobfamily': 'Marktfolge', 'OE-Cluster': 'Markt'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2026-12-31', vacancies=vacancies)
    assert not nh.empty, 'No New_Hire events in D1'
    hired = nh[nh['Jobfamily'] == 'Marktfolge']
    assert len(hired) >= 1, 'Vakanz-Jobfamily nicht geerbt'
    assert hired.iloc[0]['org_unit'] == 'OE2'

def D2():
    vacancies_future = [{'date': pd.Timestamp('2027-06-01'), 'org_unit': 'OE2', 'planstelle': 'X', 'Jobfamily': 'Spezial', 'OE-Cluster': 'X'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2026-06-30', vacancies=vacancies_future)
    spezial = nh[nh['Jobfamily'] == 'Spezial']
    assert len(spezial) == 0, 'Zukünftige Vakanz wurde vorzeitig konsumiert'

def D3():
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies'}), vacancies=[])
    assert len(nh) > 0, 'Ohne Vakanzen sollte Fallback auf Random greifen'

def D4():
    vacancies_multi = [
        {'date': pd.Timestamp('2026-01-15'), 'org_unit': 'OE1', 'planstelle': 'P1', 'Jobfamily': 'JF_FIRST', 'OE-Cluster': 'C1'},
        {'date': pd.Timestamp('2026-01-20'), 'org_unit': 'OE2', 'planstelle': 'P2', 'Jobfamily': 'JF_SECOND', 'OE-Cluster': 'C2'},
    ]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 10, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2026-12-31', vacancies=vacancies_multi)
    assert not nh.empty, 'No New_Hire events in D4'
    jf_order = nh['Jobfamily'].tolist()
    idx_first = next((i for i, j in enumerate(jf_order) if j == 'JF_FIRST'), None)
    idx_second = next((i for i, j in enumerate(jf_order) if j == 'JF_SECOND'), None)
    if idx_first is not None and idx_second is not None:
        assert idx_first < idx_second, 'FIFO verletzt: JF_SECOND vor JF_FIRST konsumiert'

def D5():
    vacancies_one = [{'date': pd.Timestamp('2026-02-01'), 'org_unit': 'OE2', 'planstelle': 'Einzel', 'Jobfamily': 'UniqueJF', 'OE-Cluster': 'X'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 10, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2027-12-31', vacancies=vacancies_one)
    c = nh['Jobfamily'].tolist().count('UniqueJF')
    assert c == 1, f'Vakanz wurde {c}x konsumiert'

# E

def E1():
    dist = [
        {'Jobfamily': 'Markt', 'OE-Cluster': 'Markt', 'Share %': 70},
        {'Jobfamily': 'Stab', 'OE-Cluster': 'Stab', 'Share %': 30},
    ]
    _, nh = run(make_snap(), make_params({'count_per_year': 50, 'strategy': 'Random', 'distribution': dist}), start='2026-01-01', end='2027-12-31')
    assigned = nh['Jobfamily'].unique().tolist()
    assert 'Markt' in assigned, f"Jobfamily 'Markt' fehlt: {assigned}"
    assert 'Stab' in assigned, f"Jobfamily 'Stab' fehlt: {assigned}"
    pct_markt = (nh['Jobfamily'] == 'Markt').mean()
    assert 0.55 <= pct_markt <= 0.85, f'Markt-Anteil unerwartet: {pct_markt:.0%}'

def E2():
    dist_invalid = [{'Jobfamily': 'X', 'OE-Cluster': 'Y', 'Share %': 0.0}]
    try:
        _, _ = run(make_snap(), make_params({'count_per_year': 5, 'strategy': 'Random', 'distribution': dist_invalid}))
    except Exception as ex:
        raise AssertionError(f'Ungültige Matrix crasht: {ex}')

def E3():
    vacancies = [{'date': pd.Timestamp('2026-02-01'), 'org_unit': 'OE1', 'planstelle': 'X', 'Jobfamily': 'VacancyJF', 'OE-Cluster': 'C'}]
    dist = [{'Jobfamily': 'MatrixJF', 'OE-Cluster': 'M', 'Share %': 100}]
    _, nh = run(make_snap(), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies', 'distribution': dist}), start='2026-01-01', end='2026-12-31', vacancies=vacancies)
    assert not nh.empty, 'No New_Hire events in E3'
    assert nh.iloc[0]['Jobfamily'] == 'VacancyJF', f"Matrix hat Vakanz-JF überschrieben: {nh.iloc[0]['Jobfamily']}"

# F

def F1():
    _, nh = run(make_snap(), make_params({'count_per_year': 10}))
    assert (nh['mak'] == 1.0).all()
    assert nh['mak'].sum() == len(nh)

def F2():
    _, nh = run(make_snap(), make_params({'count_per_year': 10}))
    assert (nh['count'] == 1).all()

# G

def G1():
    p = make_params({'count_per_year': 10})
    _, nh1 = run(make_snap(), p)
    _, nh2 = run(make_snap(), p)
    assert len(nh1) == len(nh2)
    assert list(pd.to_datetime(nh1['date']).values) == list(pd.to_datetime(nh2['date']).values)
    assert list(nh1['persnr'].values) == list(nh2['persnr'].values)

def G2():
    p1 = make_params({'count_per_year': 10}, seed=42)
    p2 = make_params({'count_per_year': 10}, seed=99)
    _, nh1 = run(make_snap(), p1)
    _, nh2 = run(make_snap(), p2)
    assert list(pd.to_datetime(nh1['date']).values) != list(pd.to_datetime(nh2['date']).values), 'Seed hat keine Wirkung'

# H

def H1():
    p = make_params({'count_per_year': 10})
    p['new_hires']['active'] = False
    _, nh = run(make_snap(), p)
    assert len(nh) == 0

def H2():
    empty = pd.DataFrame(columns=['PersNr','Organisationseinheit','Jobfamily','active','mak','TrfGr','Eintritt'])
    try:
        _, _ = run(empty, make_params({'count_per_year': 5}))
    except Exception as ex:
        raise AssertionError(f'Leerer Snapshot crasht: {ex}')

def H3():
    _, nh = run(make_snap(), make_params({'count_per_year': 50}), start='2026-01-01', end='2027-12-31')
    dups = len(nh) - nh['persnr'].nunique()
    assert nh['persnr'].nunique() == len(nh), f'ID-Kollisionen: {dups} Duplikate'

def H4():
    _, nh = run(make_snap(), make_params({'count_per_year': 10}), start='2026-01-01', end='2027-06-30')
    dates = pd.to_datetime(nh['date'])
    assert (dates >= pd.Timestamp('2026-01-01')).all()
    assert (dates <= pd.Timestamp('2027-06-30')).all()

def H5():
    _, nh = run(make_snap(), make_params({'count_per_year': 10}), start='2026-01-01', end='2027-06-30')
    if 'is_internal_transition' in nh.columns:
        assert not nh['is_internal_transition'].fillna(False).any(), 'New_Hire fälschlicherweise als interne Transition markiert'

# Critical

def K1_date_filter():
    vacancies = [{'date': pd.Timestamp('2027-06-01'), 'org_unit': 'OE2', 'planstelle': 'X', 'Jobfamily': 'FutureJF', 'OE-Cluster': 'X'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 20, 'strategy': 'Fill Vacancies'}), start='2027-01-01', end='2027-05-31', vacancies=vacancies)
    assert 'FutureJF' not in nh['Jobfamily'].values, 'Future vacancy consumed before date'

def K2_consume_once():
    vacancies = [{'date': pd.Timestamp('2026-01-01'), 'org_unit': 'OE1', 'planstelle': 'X', 'Jobfamily': 'UniqueOnce', 'OE-Cluster': 'C'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 20, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2027-12-31', vacancies=vacancies)
    assert nh['Jobfamily'].tolist().count('UniqueOnce') == 1

def K3_matrix_vs_replacement():
    vacancies = [{'date': pd.Timestamp('2026-01-01'), 'org_unit': 'OE1', 'planstelle': 'X', 'Jobfamily': 'VacJF', 'OE-Cluster': 'VC'}]
    dist = [{'Jobfamily': 'MatrixJF', 'OE-Cluster': 'MC', 'Share %': 100}]
    _, nh = run(make_snap(['OE1']), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies', 'distribution': dist}), start='2026-01-01', end='2026-12-31', vacancies=vacancies)
    assert not nh.empty, 'No New_Hire events in K3'
    assert nh.iloc[0]['Jobfamily'] == 'VacJF'

def K4_source_camelcase():
    _, nh = run(make_snap(), make_params({'count_per_year': 5}))
    assert (nh['source'] == 'NewHire').all(), nh['source'].unique().tolist()

def K5_fill_fallback():
    # all vacancies in future, but should still hire via fallback
    vacancies = [{'date': pd.Timestamp('2030-01-01'), 'org_unit': 'OE2', 'planstelle': 'X', 'Jobfamily': 'NeverNow', 'OE-Cluster': 'N'}]
    _, nh = run(make_snap(['OE1','OE2']), make_params({'count_per_year': 5, 'strategy': 'Fill Vacancies'}), start='2026-01-01', end='2026-12-31', vacancies=vacancies)
    assert len(nh) > 0


cases = [
    ('A','A1 Pflichtfelder',A1),('A','A2 Feldwerte',A2),('A','A3 Kein Lifecycle',A3),
    ('B','B1 Target +/-1',B1),('B','B2 count=0',B2),('B','B3 High volume',B3),
    ('C','C1 Random units known',C1),('C','C2 OrgUnit fixed target',C2),
    ('D','D1 Vacancy consume + inherit',D1),('D','D2 Vacancy date filter',D2),('D','D3 Fill fallback empty list',D3),('D','D4 FIFO order',D4),('D','D5 Vacancy consume once',D5),
    ('E','E1 Distribution weighting',E1),('E','E2 Invalid distribution fallback',E2),('E','E3 Replacement ignores matrix',E3),
    ('F','F1 MAK immediate',F1),('F','F2 HC +1',F2),
    ('G','G1 Same seed deterministic',G1),('G','G2 Different seed different',G2),
    ('H','H1 active false',H1),('H','H2 Empty snapshot no crash',H2),('H','H3 ID uniqueness',H3),('H','H4 date bounds',H4),('H','H5 no internal transition',H5),
]

for b,n,f in cases:
    run_case(b,n,f)

run_critical('K1 Date filter on vacancies', K1_date_filter)
run_critical('K2 Vacancy consumed exactly once', K2_consume_once)
run_critical('K3 Replacement ignores distribution matrix', K3_matrix_vs_replacement)
run_critical('K4 source field exact NewHire', K4_source_camelcase)
run_critical('K5 Fill Vacancies fallback works', K5_fill_fallback)

for b,n,s,d in results:
    print(f'[{b}] {n}: {s}')
    if s != 'PASS':
        print('---DETAIL---')
        print(d)
        print('---END---')

print('\nCRITICAL_SUMMARY')
for n,s,d in critical:
    print(f'{n}: {s}')
    if s != 'PASS':
        print('---DETAIL---')
        print(d)
        print('---END---')

print('\nBLOCK_SUMMARY')
blk = defaultdict(list)
for r in results:
    blk[r[0]].append(r)
for b in ['A','B','C','D','E','F','G','H']:
    arr = blk[b]
    p = sum(1 for x in arr if x[2]=='PASS')
    f = sum(1 for x in arr if x[2]=='FAIL')
    e = sum(1 for x in arr if x[2]=='ERROR')
    print(f'{b}: PASS={p} FAIL={f} ERROR={e}')
