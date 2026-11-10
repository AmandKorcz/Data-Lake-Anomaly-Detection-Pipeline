
import pandas as pd
import pytest

from src.processing.prepare_ke24_processed import add_period_key


@pytest.mark.parametrize(
    "period, period_year, expected",
    [
        (1, "1.2026", "2026-01"),
        (1, "01.2026", "2026-01"),
        (1, "001.2026", "2026-01"),
        (9, "009.2025", "2025-09"),
        (12, "012.2025", "2025-12"),
    ],
)
def test_valid_period_year_formats(period, period_year, expected):
    df = pd.DataFrame({
        "period": [period],
        "period_year": [period_year],
    })

    result = add_period_key(df)

    assert result["period_key"].iloc[0] == expected


@pytest.mark.parametrize(
    "period, period_year",
    [
        (1, "013.2026"),
        (2, "001.2026"),
        (1, "ABC.2026"),
    ],
)
def test_invalid_period_year_formats(period, period_year):
    df = pd.DataFrame({
        "period": [period],
        "period_year": [period_year],
    })

    with pytest.raises(ValueError):
        add_period_key(df)
