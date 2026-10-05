import numpy as np
import pandas as pd
import pytest

from ml.evaluate_var_ensemble import adaptive_prediction


@pytest.mark.parametrize("mode",["calibrated","adaptive","gated"])
def test_ensemble_cannot_learn_from_unreleased_targets(mode):
    origins=pd.date_range("2026-09-20T00:00:00Z",periods=12,freq="h")
    bank=np.full((4,12,4,12),100.0)
    bank[1]=105.0
    truth=np.full((12,4,12),110.0)
    expected=adaptive_prediction(bank,truth,origins,10,mode)
    # At origin 10, only target windows ending by 09:30 are observable.
    truth[9:]=100000.0
    bank[:,11:]=100000.0
    assert adaptive_prediction(bank,truth,origins,10,mode) == pytest.approx(expected)


def test_calibration_is_bounded_and_requires_history():
    origins=pd.date_range("2026-09-20T00:00:00Z",periods=12,freq="h")
    bank=np.full((4,12,4,12),100.0)
    truth=np.full((12,4,12),1000.0)
    assert adaptive_prediction(bank,truth,origins,1,"calibrated") == pytest.approx(bank[0,1])
    assert adaptive_prediction(bank,truth,origins,10,"calibrated") == pytest.approx(np.full((4,12),115.0))


def test_gated_complement_requires_observed_improvement():
    origins=pd.date_range("2026-09-20T00:00:00Z",periods=12,freq="h")
    bank=np.full((2,12,4,12),100.0)
    bank[1]=120.0
    truth=np.full((12,4,12),100.0)
    assert adaptive_prediction(bank,truth,origins,10,"gated") == pytest.approx(bank[0,10])
    truth[:9]=120.0
    assert adaptive_prediction(bank,truth,origins,10,"gated") == pytest.approx(np.full((4,12),110.0))
