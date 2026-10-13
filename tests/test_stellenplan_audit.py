import io

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from dataloader.stellenplan_audit import build_stellenplan_audit, stellenplan_matrix


def test_reconciliation_preserves_bad_rows_and_does_not_multiply_duplicates():
    ma = pd.DataFrame({'PersNr': [1, 2, 2, 3, None], 'BsGrd': [100, 50, 50, 75, 100]})
    pl = pd.DataFrame({'Personalnummer': [1, 2, 9, None], 'Sollarbeitszeit': [39, 20, 0, 39]})
    audit = build_stellenplan_audit(ma, pl)
    assert len(audit['positions']) == 4
    assert audit['positions'].Zuordnung.tolist() == ['Eindeutig besetzt', 'Mitarbeiterzuordnung mehrdeutig', 'Mitarbeiterdatensatz fehlt', 'Unbesetzt']
    counts = list(audit['employee_counts'].values())
    assert counts[0] - sum(counts[1:-1]) == counts[-1] == 1
    assert audit['positions']['MAK ungeklärt'].sum() == 2
    assert audit['positions']['Ist-MAK (39h)'].sum() == 1
    assert len(ma) == 5 and 'Zuordnung' not in pl


@pytest.mark.parametrize('hours', [[39, 39], [0, 0], [39, 0]])
def test_capacity_is_counted_once_per_person(hours):
    audit = build_stellenplan_audit(pd.DataFrame({'PersNr': [1], 'BsGrd': [40]}),
        pd.DataFrame({'Personalnummer': [1, 1], 'Sollarbeitszeit': hours}))
    assert audit['positions']['Ist-MAK (39h)'].sum() == pytest.approx(.4)


def test_zero_soll_is_not_zero_ist_and_invalid_capacity_is_unknown():
    audit = build_stellenplan_audit(pd.DataFrame({'PersNr': [1, 2, 3], 'BsGrd': [100, None, -5]}),
        pd.DataFrame({'Personalnummer': [1, 2, 3, None], 'Sollarbeitszeit': [0, 40, None, 20]}))
    assert audit['positions']['Ist-MAK (39h)'].iloc[0] == 1
    assert audit['positions']['MAK ungeklärt'].tolist() == [False, True, True, False]
    assert 'Sollstunden unklar / abweichend' in stellenplan_matrix(audit['positions'])


def test_empty_input():
    audit = build_stellenplan_audit(pd.DataFrame(columns=['PersNr']), pd.DataFrame(columns=['Personalnummer']))
    assert audit['positions'].empty
    assert len(stellenplan_matrix(audit['positions'])) == 5


@pytest.mark.parametrize('with_type', [False, True])
def test_ui_supports_optional_employee_type(with_type):
    ma = pd.DataFrame({'PersNr': [1, 2], 'BsGrd': [100, 50]})
    pl = pd.DataFrame({'Personalnummer': [1, None], 'Sollarbeitszeit': [0, 39], 'Kürzel OrgEinheit': ['A', 'B']})
    if with_type:
        pl['MA-Typ'] = ['Angestellte', None]
    buffers = []
    for df in [ma, pl]:
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        buffers.append(buf.getvalue())
    app = AppTest.from_string('import streamlit as st\nfrom components.stellenplan_audit import render_stellenplan_audit\nrender_stellenplan_audit(*st.session_state["inputs"])')
    app.session_state['inputs'] = buffers
    app.run(timeout=30)
    assert not app.exception
    assert len(app.tabs) == 3
    assert len(app.get('plotly_chart')) == 2
    app.multiselect(key='integrity_filter_Kürzel OrgEinheit').set_value(['A']).run()
    assert not app.exception
    assert len(app.dataframe[1].value) == 1
