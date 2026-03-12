import sys
import traceback
from collections import defaultdict

import pandas as pd

sys.path.insert(0, 'KSK_Layout')
from zugaenge.forecast import run_forecast_zugaenge


def make_snap(n_units=1):
    rows = []
    for i in range(n_units):
        rows.append({
            'PersNr': f'E{i+1:03d}',
            'Organisationseinheit': f'OE{i+1}',
            'Jobfamily': 'Angestellte',
            'active': True,
            'mak': 1.0,
            'TrfGr': 'E9A',
            'Eintritt': pd.Timestamp('2015-01-01'),
            'OE-Cluster': f'C{i+1}',
            'JF-Cluster': f'J{i+1}',
        })
    return pd.DataFrame(rows)


def make_params(tr=None, seed=42):
    p = {
        'azubi': {
            'active': False,
            'new_cases_per_year': 0,
            'duration_years': 3,
            'retention_rate': 1.0,
            'strategy': 'Random',
            'entry_tariff_group': 'E5',
            'entry_step': 1,
            'exclude_baseline_azubis': False,
            'azubi_mak_during_training': 0.0,
            'azubi_mak_after_takeover': 1.0,
            'azubi_conversion_month': 8,
            'azubi_conversion_day': 1,
            'graduation_mode': 'nearest_cycle',
            'nearest_cycle_grace_days': None,
            'use_takeover_matrix': False,
            'takeover_matrix': {},
            'takeover_dimension': 'JobFamily',
            'jf_to_cluster_map': {},
        },
        'trainee': {
            'active': True,
            'new_cases_per_year': 5,
            'duration_years': 1.5,
            'salary_group': 'E13',
            'strategy': 'Random',
            'target_org_unit': None,
        },
        'new_hires': {'active': False, 'count_per_year': 0, 'strategy': 'Random'},
        'random_seed': seed,
    }
    if tr:
        p['trainee'].update(tr)
    return p


def run(snap, params, start='2026-01-01', end='2028-12-31'):
    result = run_forecast_zugaenge(
        df_snapshot=snap,
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        params=params,
    )
    ev = result.get('events', pd.DataFrame())
    tr = ev[ev['type'] == 'Trainee_Hire'] if not ev.empty else pd.DataFrame()
    return ev, tr


results = []
critical = []

def record(target, key, status, detail=''):
    target.append((key, status, detail))


def run_case(block, name, fn):
    try:
        fn()
        results.append((block, name, 'PASS', ''))
    except AssertionError as e:
        results.append((block, name, 'FAIL', f'{e}\n{traceback.format_exc()}'))
    except Exception as e:
        results.append((block, name, 'ERROR', f'{e}\n{traceback.format_exc()}'))


# Block A

def A1():
    required = ['date', 'type', 'count', 'persnr', 'org_unit', 'source', 'mak', 'TrfGr', 'Jobfamily', 'Planstelle']
    ev, tr = run(make_snap(), make_params({'new_cases_per_year': 5}))
    assert not tr.empty, 'No trainee events'
    for field in required:
        assert field in tr.columns, f'Feld fehlt: {field}'

def A2():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 5}))
    assert (tr['type'] == 'Trainee_Hire').all()
    assert (tr['count'] == 1).all()
    assert (tr['mak'] == 1.0).all()
    assert (tr['source'] == 'Trainee').all()
    assert (tr['Jobfamily'] == 'Trainee').all()
    assert (tr['Planstelle'] == 'Trainee').all()

def A3():
    ev_long, _ = run(make_snap(), make_params({'new_cases_per_year': 10}), start='2026-01-01', end='2031-12-31')
    grad_types = ['Trainee_Conversion_Out', 'Trainee_Conversion_In', 'Trainee_Exit', 'Trainee_Graduation']
    assert not ev_long['type'].isin(grad_types).any(), 'Unerwartete Graduation-Events'

# Block B

def B1():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 12}), start='2026-01-01', end='2028-12-31')
    assert abs(len(tr) - 36) <= 1, f'Erwartet ~36, erhalten {len(tr)}'

def B2():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 0}))
    assert len(tr) == 0

def B3():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 1}), start='2026-01-01', end='2026-12-31')
    assert abs(len(tr) - 1) <= 1, f'Erwartet ~1, erhalten {len(tr)}'

def B4():
    _, tr = run(make_snap(3), make_params({'new_cases_per_year': 100}), start='2026-01-01', end='2027-12-31')
    assert abs(len(tr) - 200) <= 2, f'Erwartet ~200, erhalten {len(tr)}'

# Block C

def C1():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 12}))
    assert (tr['mak'] == 1.0).all(), 'Trainee muss sofort MAK=1.0 haben'

def C2():
    _, tr = run(make_snap(), make_params({'new_cases_per_year': 12}))
    total_mak_delta = tr['mak'].sum()
    assert total_mak_delta == len(tr) * 1.0, f'MAK-Delta falsch: {total_mak_delta}'

def C3():
    ev, _ = run(make_snap(), make_params({'new_cases_per_year': 12}))
    conv_out = ev[ev['type'] == 'Trainee_Conversion_Out'] if not ev.empty else pd.DataFrame()
    assert len(conv_out) == 0

# Block D

def D1():
    p = make_params({'new_cases_per_year': 10})
    _, tr1 = run(make_snap(), p)
    _, tr2 = run(make_snap(), p)
    assert len(tr1) == len(tr2)
    assert list(pd.to_datetime(tr1['date']).values) == list(pd.to_datetime(tr2['date']).values)
    assert list(tr1['persnr'].values) == list(tr2['persnr'].values)

def D2():
    p1 = make_params({'new_cases_per_year': 10}, seed=42)
    p2 = make_params({'new_cases_per_year': 10}, seed=99)
    _, tr1 = run(make_snap(), p1)
    _, tr2 = run(make_snap(), p2)
    assert len(tr1) == len(tr2), 'Counts bei verschiedenem Seed sollten gleich sein'
    assert list(pd.to_datetime(tr1['date']).values) != list(pd.to_datetime(tr2['date']).values), 'Seed hat keine Wirkung'

# Block E

def E1():
    for sg in ['E13', 'E9A', 'E6', 'TVAöD']:
        _, tr_sg = run(make_snap(), make_params({'new_cases_per_year': 5, 'salary_group': sg}))
        assert (tr_sg['TrfGr'] == sg).all(), f'salary_group={sg} nicht korrekt: {tr_sg["TrfGr"].unique()}'

def E2():
    _, tr_short = run(make_snap(), make_params({'new_cases_per_year': 5, 'duration_years': 0.5}))
    _, tr_long = run(make_snap(), make_params({'new_cases_per_year': 5, 'duration_years': 3.0}))
    assert len(tr_short) == len(tr_long), f'duration_years sollte keinen Effekt haben: 0.5->{len(tr_short)}, 3.0->{len(tr_long)}'

# Block F

def F1():
    snap3 = make_snap(3)
    known = snap3['Organisationseinheit'].unique().tolist()
    _, tr_r = run(snap3, make_params({'new_cases_per_year': 20, 'strategy': 'Random'}))
    unknown = [u for u in tr_r['org_unit'].unique() if u not in known]
    assert not unknown, f'Unbekannte Units: {unknown}'

def F2():
    _, tr_ou = run(make_snap(3), make_params({'new_cases_per_year': 20, 'strategy': 'OrgUnit', 'target_org_unit': 'OE2'}))
    assert (tr_ou['org_unit'] == 'OE2').all()

def F3():
    _, tr_fv = run(make_snap(3), make_params({'new_cases_per_year': 10, 'strategy': 'Fill Vacancies'}))
    assert len(tr_fv) > 0

# Block G

def G1():
    p_off = make_params()
    p_off['trainee']['active'] = False
    _, tr_off = run(make_snap(), p_off)
    assert len(tr_off) == 0

def G2():
    empty = pd.DataFrame(columns=['PersNr', 'Organisationseinheit', 'Jobfamily', 'active', 'mak', 'TrfGr', 'Eintritt'])
    try:
        _, _ = run(empty, make_params({'new_cases_per_year': 5}))
    except Exception as ex:
        raise AssertionError(f'Leerer Snapshot crasht: {ex}')

def G3():
    _, tr_ids = run(make_snap(), make_params({'new_cases_per_year': 50}), start='2026-01-01', end='2028-12-31')
    unique = tr_ids['persnr'].nunique()
    total = len(tr_ids)
    assert unique == total, f'ID-Kollisionen: {total - unique} Duplikate'

def G4():
    _, tr_d = run(make_snap(), make_params({'new_cases_per_year': 10}), start='2026-01-01', end='2027-12-31')
    dates = pd.to_datetime(tr_d['date'])
    assert (dates >= pd.Timestamp('2026-01-01')).all()
    assert (dates <= pd.Timestamp('2027-12-31')).all()

def G5():
    _, tr_d = run(make_snap(), make_params({'new_cases_per_year': 10}), start='2026-01-01', end='2027-12-31')
    if 'is_internal_transition' in tr_d.columns:
        assert not tr_d['is_internal_transition'].fillna(False).any()

# Critical checks

def K1_duration_no_effect():
    _, tr_short = run(make_snap(), make_params({'new_cases_per_year': 7, 'duration_years': 0.5}), start='2026-01-01', end='2028-12-31')
    _, tr_long = run(make_snap(), make_params({'new_cases_per_year': 7, 'duration_years': 5.0}), start='2026-01-01', end='2028-12-31')
    assert len(tr_short) == len(tr_long), (len(tr_short), len(tr_long))

def K2_no_graduation_even_after_duration():
    ev, _ = run(make_snap(), make_params({'new_cases_per_year': 8, 'duration_years': 1.5}), start='2026-01-01', end='2031-12-31')
    banned = ['Trainee_Conversion_Out', 'Trainee_Conversion_In', 'Trainee_Exit', 'Trainee_Graduation']
    assert not ev['type'].isin(banned).any(), ev[ev['type'].isin(banned)].head().to_dict('records')

def K3_trainee_vs_azubi_mak():
    snap = make_snap()
    p = make_params({'new_cases_per_year': 6})
    p['azubi']['active'] = True
    p['azubi']['new_cases_per_year'] = 6
    p['new_hires']['active'] = False
    ev, _ = run(snap, p, start='2026-01-01', end='2026-12-31')
    tr = ev[ev['type'] == 'Trainee_Hire']
    az = ev[ev['type'] == 'Azubi_Hire']
    assert not tr.empty and not az.empty, 'Need both Trainee_Hire and Azubi_Hire events'
    assert (tr['mak'] == 1.0).all(), tr['mak'].unique()
    assert (az['mak'] == 0.0).all(), az['mak'].unique()

def K4_seed_deterministic_ids():
    p = make_params({'new_cases_per_year': 10}, seed=42)
    _, tr1 = run(make_snap(), p, start='2026-01-01', end='2028-12-31')
    _, tr2 = run(make_snap(), p, start='2026-01-01', end='2028-12-31')
    assert list(tr1['persnr']) == list(tr2['persnr'])


def run_critical(name, fn):
    try:
        fn()
        record(critical, name, 'PASS', '')
    except AssertionError as e:
        record(critical, name, 'FAIL', f'{e}\n{traceback.format_exc()}')
    except Exception as e:
        record(critical, name, 'ERROR', f'{e}\n{traceback.format_exc()}')


cases = [
    ('A','A1 Pflichtfelder',A1),('A','A2 Feldwerte',A2),('A','A3 kein Graduation-Event',A3),
    ('B','B1 Volumen ~Target',B1),('B','B2 zero count',B2),('B','B3 debt uebers Jahr',B3),('B','B4 high-volume stress',B4),
    ('C','C1 MAK sofort',C1),('C','C2 Netto-MAK steigt',C2),('C','C3 kein conversion pair',C3),
    ('D','D1 gleicher Seed identisch',D1),('D','D2 unterschiedlicher Seed unterschiedlich',D2),
    ('E','E1 salary_group mapping',E1),('E','E2 duration ohne Effekt',E2),
    ('F','F1 Random nur bekannte Units',F1),('F','F2 OrgUnit zielgenau',F2),('F','F3 Fill Vacancies fallback',F3),
    ('G','G1 active=False',G1),('G','G2 leerer Snapshot',G2),('G','G3 ID-Eindeutigkeit',G3),('G','G4 dates im Zeitraum',G4),('G','G5 no internal transition',G5),
]

for b,n,f in cases:
    run_case(b,n,f)

run_critical('K1 duration_years ohne Effekt', K1_duration_no_effect)
run_critical('K2 kein Graduation-Lifecycle', K2_no_graduation_even_after_duration)
run_critical('K3 MAK Trainee(1.0) vs Azubi(0.0)', K3_trainee_vs_azubi_mak)
run_critical('K4 Seed-Deterministik fuer PersNr', K4_seed_deterministic_ids)

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
for b in ['A','B','C','D','E','F','G']:
    arr = blk[b]
    p = sum(1 for x in arr if x[2]=='PASS')
    f = sum(1 for x in arr if x[2]=='FAIL')
    e = sum(1 for x in arr if x[2]=='ERROR')
    print(f'{b}: PASS={p} FAIL={f} ERROR={e}')
