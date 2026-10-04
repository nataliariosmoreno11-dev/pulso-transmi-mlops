import pandas as pd
import pytest
from ml.train_lightgbm import regularize_observations
from pulso_transmi.submit_current_cycle import build_features, latest_complete_cycle_accuracy, observation_demand, recent_meta_adjustments, validate_predictions


def test_observation_demand_accepts_stream_v1_and_v2():
    assert observation_demand({"demand": 17}) == 17
    assert observation_demand({"schema_version": 2, "measurement": {"value": "23.00", "unit": "passengers", "quality": "ok"}}) == 23
    assert observation_demand({"schema_version": 2, "measurement": {"value": None, "quality": "missing"}}) is None


def test_observation_demand_rejects_fractional_counts():
    with pytest.raises(RuntimeError, match="conteo entero"):
        observation_demand({"schema_version": 2, "measurement": {"value": "23.5"}})


def test_training_grid_fills_isolated_gap_with_past_value():
    frame=pd.DataFrame({
        "station_id":["02300","02300"],
        "observed_at":pd.to_datetime(["2026-09-20T10:00:00Z","2026-09-20T10:30:00Z"]),
        "demand":[100,120],
    })
    regular=regularize_observations(frame)
    filled=regular.loc[regular.observed_at == pd.Timestamp("2026-09-20T10:15:00Z"),"demand"]
    assert filled.iloc[0] == 100

def cycle():
    return {"cycle_id":"cyc_test","origin_at":"2026-09-11T09:00:00Z","data_cutoff":"2026-09-11T09:00:00Z","expected_predictions":1,
            "targets":[{"station_id":"02300","target_at":"2026-09-11T09:15:00Z","horizon_minutes":15}]}

def test_features_respect_available_history():
    ts=pd.date_range("2026-09-04T00:00:00Z","2026-09-11T09:00:00Z",freq="15min")
    history=pd.DataFrame({"station_id":"02300","observed_at":ts,"demand":range(len(ts))})
    names=["station_id","horizon_steps","slot","day_of_week","is_weekend","lag_available","lag_15m","lag_1h","lag_2h","lag_day","lag_2days","lag_week"]
    frame=build_features(history,cycle(),names)
    lookup=history.set_index("observed_at")["demand"]
    assert frame.loc[0,"lag_available"]==lookup[pd.Timestamp("2026-09-11T08:30:00Z")]
    assert frame.loc[0,"lag_day"]==lookup[pd.Timestamp("2026-09-10T09:15:00Z")]


def test_features_fill_isolated_missing_lag_from_previous_observation():
    ts=pd.date_range("2026-09-04T00:00:00Z","2026-09-11T09:00:00Z",freq="15min")
    history=pd.DataFrame({"station_id":"02300","observed_at":ts,"demand":range(len(ts))})
    missing=pd.Timestamp("2026-09-11T08:30:00Z")
    history=history[history.observed_at != missing]
    names=["station_id","horizon_steps","slot","day_of_week","is_weekend","lag_available","lag_15m","lag_1h","lag_2h","lag_day","lag_2days","lag_week"]
    frame=build_features(history,cycle(),names)
    expected=history.set_index("observed_at").loc[pd.Timestamp("2026-09-11T08:15:00Z"),"demand"]
    assert frame.loc[0,"lag_available"] == expected

def test_validator_rejects_missing_target():
    with pytest.raises(RuntimeError,match="no coinciden"):
        validate_predictions(cycle(),[])


def test_weighted_median_uses_prediction_as_wape_weight():
    from pulso_transmi.submit_current_cycle import weighted_median
    assert weighted_median([0.8, 1.0, 1.2], [1, 10, 1]) == 1.0


def test_model_version_rejects_unsupported_characters():
    import pytest
    from pulso_transmi.submit_current_cycle import validate_model_version
    assert validate_model_version("lightgbm-tournament:20260924T203613Z-station-cal-v1")
    with pytest.raises(ValueError):
        validate_model_version("modelo+calibrado")


class _Cursor:
    def __init__(self, rows):
        self.rows = rows
    def __enter__(self):
        return self
    def __exit__(self, *_):
        return False
    def execute(self, *_):
        pass
    def fetchall(self):
        return self.rows
    def fetchone(self):
        return self.rows[0] if self.rows else None


class _Connection:
    def __init__(self, rows):
        self.rows = rows
    def cursor(self):
        return _Cursor(self.rows)


def test_latest_complete_cycle_accuracy_reads_metric():
    assert latest_complete_cycle_accuracy(_Connection([(78.68,)])) == pytest.approx(78.68)


def test_recent_adjustment_uses_stable_rolling_bias_ratio():
    rows = [("02300", 100.0, 110.0, 70.0, 90.0, n) for n in range(1, 25)]
    adjustment = recent_meta_adjustments(_Connection(rows), "2026-09-11T09:00:00Z")
    use_lag, use_lag4, factor = adjustment["02300"]
    assert not use_lag
    assert not use_lag4
    assert factor == pytest.approx(1.10)


def test_recent_adjustment_adapts_to_confirmed_regime_shift():
    old = [("03000", 100.0, 100.0, 80.0, 60.0, n) for n in range(9, 25)]
    recent = [("03000", 100.0, 55.0, 80.0, 56.0, n) for n in range(1, 9)]
    adjustment = recent_meta_adjustments(_Connection(recent + old), "2026-09-11T09:00:00Z")
    use_lag, use_lag4, factor = adjustment["03000"]
    assert not use_lag
    assert use_lag4
    assert factor == pytest.approx(1.0)


def test_recent_adjustment_uses_safe_lag_when_model_collapses():
    rows = [("05100", 220.0, 100.0, 105.0, 98.0, n) for n in range(1, 25)]
    use_lag, use_lag4, factor = recent_meta_adjustments(
        _Connection(rows), "2026-09-11T09:00:00Z"
    )["05100"]
    assert not use_lag
    assert use_lag4
    assert factor == pytest.approx(1.0)


def test_recent_adjustment_keeps_four_hour_strategy_on_tie():
    rows = [("07107", 100.0, 102.0, 70.0, 100.5, n) for n in range(1, 25)]
    use_lag, use_lag4, factor = recent_meta_adjustments(
        _Connection(rows), "2026-09-11T09:00:00Z"
    )["07107"]
    assert not use_lag
    assert use_lag4
    assert factor == pytest.approx(1.0)
