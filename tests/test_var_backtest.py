import numpy as np
import pytest

from ml.backtest_var import cycle_accuracy


def test_backtest_weights_stations_equally_and_clamps_negative_accuracy():
    actual=np.array([[10.0,1000.0],[10.0,1000.0]])
    predicted=np.array([[40.0,1000.0],[40.0,1000.0]])
    assert cycle_accuracy(actual,predicted) == pytest.approx(50.0)


def test_backtest_zero_demand_does_not_produce_nan():
    assert cycle_accuracy(np.zeros((4,12)),np.zeros((4,12))) == 0.0
