from utils.monte_carlo_filter_options import filter_options_from_ensemble


def test_sidebar_options_include_groups_from_later_runs():
    ensemble = {"statistics": {"annual_stock_samples": [
        ("2027-12-31", "Organisationseinheit", "Neu", "heads", [0, 2]),
        ("2027-12-31", "Jobfamily", "Neue Familie", "heads", [0, 1]),
        ("2027-12-31", "Jobfamily", "UNMAPPED", "heads", [1, 1]),
        ("2027-12-31", "Organisationseinheit", "Leer", "heads", [0, 0]),
    ]}}
    assert filter_options_from_ensemble(ensemble) == {
        "Organisationseinheit": ["Neu"], "Jobfamily": ["Neue Familie"]}
