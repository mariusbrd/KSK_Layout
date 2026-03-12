import sys
import traceback
from pathlib import Path

sys.path.insert(0, 'KSK_Layout')

import pandas as pd
from zugaenge.forecast import run_forecast_zugaenge, _estimate_baseline_graduation_date
from zugaenge.params import default_params


def make_snapshot(entries):
    defaults = {
        'Organisationseinheit': 'OE1',
        'active': True,
        'mak': 1.0,
        'TrfGr': 'E9A',
        'Jobfamily': 'Angestellte',
        'Eintritt': pd.Timestamp('2020-01-01'),
        'OE-Cluster': 'Cluster1',
        'JF-Cluster': 'JFCluster1',
    }
    rows = [{**defaults, **e} for e in entries]
    if not rows:
        # Preserve schema even for empty fixtures
        return pd.DataFrame(columns=list(defaults.keys()) + ["PersNr"])
    return pd.DataFrame(rows)


def make_params(overrides=None):
    p = {
        'azubi': {
            'active': True,
            'new_cases_per_year': 0,
            'duration_years': 3.0,
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
            'use_takeover_matrix': False,
            'takeover_matrix': {},
            'takeover_dimension': 'JobFamily',
            'jf_to_cluster_map': {},
        },
        'trainee': {'active': False, 'new_cases_per_year': 0},
        'new_hires': {'active': False, 'count_per_year': 0, 'strategy': 'Random'},
        'random_seed': 42,
    }
    if overrides:
        for k, v in overrides.items():
            if isinstance(v, dict) and k in p:
                p[k].update(v)
            else:
                p[k] = v
    return p


def get_events(df_snapshot, params, start='2026-01-01', end='2028-12-31'):
    res = run_forecast_zugaenge(
        df_snapshot=df_snapshot,
        start_date=pd.Timestamp(start),
        end_date=pd.Timestamp(end),
        freq='M',
        params=params,
    )
    return res.get('events', pd.DataFrame()), res


results = []

def run_case(block, name, fn):
    try:
        fn()
        results.append((block, name, 'PASS', ''))
    except AssertionError as e:
        tb = traceback.format_exc()
        results.append((block, name, 'FAIL', f'{e}\n{tb}'))
    except Exception as e:
        tb = traceback.format_exc()
        results.append((block, name, 'ERROR', f'{e}\n{tb}'))

# Block A

def A1():
    p = default_params()
    assert p['azubi']['graduation_mode'] == 'nearest_cycle', p['azubi'].get('graduation_mode')

def A2():
    entry = pd.Timestamp('2023-09-01')
    result_next = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode='next_cycle')
    result_nearest = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode='nearest_cycle')
    assert result_next == pd.Timestamp('2027-08-01'), result_next
    assert result_nearest == pd.Timestamp('2026-08-01'), result_nearest

def A3():
    snap = make_snapshot([
        {'PersNr':'AZ001','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    params = make_params({'azubi': {'new_cases_per_year': 10, 'retention_rate': 0.8}})
    events,_ = get_events(snap, params, '2026-01-01', '2030-12-31')
    grads = events[events['type'].isin(['Azubi_Conversion_In','Azubi_Exit'])]
    assert not grads.empty, 'No graduation events'
    d = pd.to_datetime(grads['date'])
    assert ((d.dt.month == 8) & (d.dt.day == 1)).all(), grads[['type','date']].head().to_dict('records')

def A4():
    snap = make_snapshot([
        {'PersNr':'BAS001','Eintritt':pd.Timestamp('2023-09-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p_near = make_params({'azubi': {'graduation_mode': 'nearest_cycle', 'retention_rate': 1.0}})
    p_next = make_params({'azubi': {'graduation_mode': 'next_cycle', 'retention_rate': 1.0}})
    e_near,_ = get_events(snap, p_near, '2026-01-01', '2026-12-31')
    e_next_2026,_ = get_events(snap, p_next, '2026-01-01', '2026-12-31')
    e_next_2027,_ = get_events(snap, p_next, '2027-01-01', '2027-12-31')
    assert (e_near['type'] == 'Azubi_Conversion_In').any(), 'nearest_cycle should convert in 2026'
    assert not (e_next_2026['type'] == 'Azubi_Conversion_In').any(), 'next_cycle should not convert in 2026'
    assert (e_next_2027['type'] == 'Azubi_Conversion_In').any(), 'next_cycle should convert in 2027'

# Block B

def _baseline_outcome(new_cases):
    snap = make_snapshot([
        {'PersNr':'BAS001','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'new_cases_per_year': new_cases, 'retention_rate': 0.2, 'graduation_mode': 'nearest_cycle'}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    b = e[e['persnr']=='BAS001']
    if (b['type']=='Azubi_Conversion_In').any():
        return 'takeover'
    if (b['type']=='Azubi_Exit').any():
        return 'exit'
    return 'none'

def B1():
    outcomes = {_n: _baseline_outcome(_n) for _n in [0,5,15,50]}
    uniq = set(outcomes.values())
    assert len(uniq) == 1, f'Baseline outcome changed across volumes: {outcomes}'

def B2():
    src = Path('KSK_Layout/zugaenge/forecast.py').read_text(encoding='utf-8')
    assert 'takeover_baseline' in src and 'takeover_forecast' in src, 'debt keys missing in source'

def B3():
    entries = []
    for i in range(10):
        entries.append({'PersNr': f'BAS{i:03d}', 'Eintritt': pd.Timestamp('2023-01-01'), 'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0})
    snap = make_snapshot(entries)
    p = make_params({'azubi': {'retention_rate': 0.8, 'new_cases_per_year': 0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    conv = e[e['type']=='Azubi_Conversion_In']['persnr'].nunique()
    ex = e[e['type']=='Azubi_Exit']['persnr'].nunique()
    assert conv == 8 and ex == 2, f'Expected 8/2, got {conv}/{ex}'

# Block C

def C1():
    snap = make_snapshot([
        {'PersNr':'BASJF1','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Bankkauffrauen/-männer','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    row = e[(e['persnr']=='BASJF1') & (e['type']=='Azubi_Conversion_In')]
    assert not row.empty, 'No conversion in event'
    r = row.iloc[0]
    assert 'Jobfamily_pre_azubi' in row.columns, 'field missing'
    assert r['Jobfamily_pre_azubi'] == 'Bankkauffrauen/-männer', r.get('Jobfamily_pre_azubi')
    assert r['Jobfamily_pre_azubi'] != r.get('Jobfamily'), 'pre and current JF should differ'

def C2():
    snap = make_snapshot([])
    p = make_params({'azubi': {'new_cases_per_year': 12, 'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    hires = e[e['type']=='Azubi_Hire']
    assert not hires.empty, 'No forecast azubi hires'
    # Expected: no explicit Jobfamily_pre_azubi for fresh forecast hires
    has_col = 'Jobfamily_pre_azubi' in hires.columns
    if has_col:
        nonnull = hires['Jobfamily_pre_azubi'].notna().any()
        assert not nonnull, 'Forecast hires should not carry Jobfamily_pre_azubi value'

# Block D

def D1():
    snap = make_snapshot([
        {'PersNr':'AZD1','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 0.5, 'new_cases_per_year': 3}})
    e,_ = get_events(snap, p, '2026-01-01', '2028-12-31')
    out = e[e['type']=='Azubi_Conversion_Out']
    inn = e[e['type']=='Azubi_Conversion_In']
    hires = e[e['type']=='Azubi_Hire']
    exits = e[e['type']=='Azubi_Exit']
    if not out.empty:
        assert out['is_internal_transition'].eq(True).all(), 'Out missing internal flag'
    if not inn.empty:
        assert inn['is_internal_transition'].eq(True).all(), 'In missing internal flag'
    if not hires.empty and 'is_internal_transition' in hires.columns:
        assert (~hires['is_internal_transition'].fillna(False)).all(), 'Hire internal flag must be false/missing'
    if not exits.empty and 'is_internal_transition' in exits.columns:
        assert (~exits['is_internal_transition'].fillna(False)).all(), 'Exit internal flag must be false/missing'

def D2():
    snap = make_snapshot([
        {'PersNr':'AZD2','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    out = e[e['type']=='Azubi_Conversion_Out'].groupby('persnr')['count'].sum()
    inn = e[e['type']=='Azubi_Conversion_In'].groupby('persnr')['count'].sum()
    common = set(out.index) & set(inn.index)
    assert common, 'No conversion pairs'
    for pid in common:
        assert out[pid] + inn[pid] == 0, f'HC not neutral for {pid}: {out[pid]} + {inn[pid]}'

def D3():
    snap = make_snapshot([
        {'PersNr':'AZD3','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    out = e[e['type']=='Azubi_Conversion_Out'][['persnr','date']]
    inn = e[e['type']=='Azubi_Conversion_In'][['persnr','date']]
    merged = out.merge(inn, on='persnr', suffixes=('_out','_in'))
    assert not merged.empty, 'No pairs to compare dates'
    d_out = pd.to_datetime(merged['date_out'])
    d_in = pd.to_datetime(merged['date_in'])
    assert (d_out == d_in).all(), merged.to_dict('records')

# Block E

def E1():
    snap = make_snapshot([])
    p = make_params({'azubi': {'new_cases_per_year': 12}})
    e,_ = get_events(snap, p, '2026-01-01', '2030-12-31')
    hires = int((e['type']=='Azubi_Hire').sum())
    expected = 12 * 5
    assert abs(hires - expected) <= 1, f'Expected ~{expected}, got {hires}'

def E2():
    snap = make_snapshot([
        {'PersNr':'AZE2','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0},
        {'PersNr':'EMP1','Eintritt':pd.Timestamp('2020-01-01'),'Jobfamily':'IT','TrfGr':'E9A','mak':1.0},
    ])
    p = make_params({'azubi': {
        'new_cases_per_year': 7,
        'retention_rate': 0.75,
        'use_takeover_matrix': True,
        'takeover_dimension': 'JobFamily',
        'takeover_matrix': {'IT': 2.0, 'Angestellte': 1.0},
    }, 'random_seed': 42})
    e1,_ = get_events(snap, p, '2026-01-01', '2028-12-31')
    e2,_ = get_events(snap, p, '2026-01-01', '2028-12-31')

    # strict equality by full row values sorted; expected to fail if non-seeded UUID is used
    s1 = e1.sort_values(['date','type','persnr']).reset_index(drop=True)
    s2 = e2.sort_values(['date','type','persnr']).reset_index(drop=True)
    assert s1.equals(s2), 'Events differ across same-seed runs'

# Block F

def F1():
    cols = ['PersNr','Organisationseinheit','active','mak','TrfGr','Jobfamily','Eintritt','OE-Cluster','JF-Cluster']
    snap = pd.DataFrame(columns=cols)
    p = make_params()
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    assert isinstance(e, pd.DataFrame)

def F2():
    snap = make_snapshot([
        {'PersNr':'AZF2','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 0.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    assert (e['type']=='Azubi_Exit').any(), 'Expected exits'
    assert not (e['type']=='Azubi_Conversion_In').any(), 'No conversion expected at retention 0'

def F3():
    snap = make_snapshot([
        {'PersNr':'AZF3','Eintritt':pd.Timestamp('2023-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    assert (e['type']=='Azubi_Conversion_In').any(), 'Expected conversion in'
    assert not (e['type']=='Azubi_Exit').any(), 'No exits expected at retention 1'

def F4():
    snap = make_snapshot([
        {'PersNr':'AZF4','Eintritt':pd.Timestamp('2026-01-01'),'Jobfamily':'Azubi','TrfGr':'TVAöD','mak':0.0}
    ])
    p = make_params({'azubi': {'duration_years': 0.5, 'retention_rate': 1.0}})
    e,_ = get_events(snap, p, '2026-01-01', '2026-12-31')
    conv = e[e['type']=='Azubi_Conversion_In']
    assert not conv.empty, 'Expected same-year graduation/convert'
    d = pd.to_datetime(conv['date'])
    assert ((d.dt.month == 8) & (d.dt.day == 1)).all(), 'August rule violated'

def F5():
    entry = pd.Timestamp('2023-08-01')
    result_next = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode='next_cycle')
    result_nearest = _estimate_baseline_graduation_date(entry, 3.0, graduation_mode='nearest_cycle')
    assert result_next == pd.Timestamp('2026-08-01'), result_next
    assert result_nearest == pd.Timestamp('2026-08-01'), result_nearest

cases = [
    ('A','A1 nearest_cycle default',A1),
    ('A','A2 next vs nearest Sept entry',A2),
    ('A','A3 graduation date always Aug-01',A3),
    ('A','A4 graduation_mode pass-through',A4),
    ('B','B1 baseline outcome stable across forecast volumes',B1),
    ('B','B2 debt keys present',B2),
    ('B','B3 retention 0.8 gives 8/2 on 10 baseline',B3),
    ('C','C1 Jobfamily_pre_azubi in Conversion_In',C1),
    ('C','C2 forecast hires no Jobfamily_pre_azubi value',C2),
    ('D','D1 internal transition flags',D1),
    ('D','D2 conversion pair HC neutral',D2),
    ('D','D3 conversion pair same date',D3),
    ('E','E1 annual-to-period cumulative accuracy',E1),
    ('E','E2 strict determinism same seed',E2),
    ('F','F1 empty snapshot no crash',F1),
    ('F','F2 retention 0.0 all exits',F2),
    ('F','F3 retention 1.0 all conversion',F3),
    ('F','F4 duration 0.5 same-year possible',F4),
    ('F','F5 entry on Aug-01 edge',F5),
]

for b,n,f in cases:
    run_case(b,n,f)

# print machine-parseable summary
for r in results:
    b,n,s,d = r
    print(f'[{b}] {n}: {s}')
    if s != 'PASS':
        print('---DETAIL---')
        print(d)
        print('---END---')

# block summary
from collections import defaultdict
blk = defaultdict(list)
for r in results:
    blk[r[0]].append(r)
print('\\nBLOCK_SUMMARY')
for b in ['A','B','C','D','E','F']:
    arr = blk[b]
    p = sum(1 for x in arr if x[2]=='PASS')
    f = sum(1 for x in arr if x[2]=='FAIL')
    e = sum(1 for x in arr if x[2]=='ERROR')
    print(f'{b}: PASS={p} FAIL={f} ERROR={e}')
