import pandas as pd
import pytest
from pulso_transmi.submit_current_cycle import build_features, validate_predictions

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

def test_validator_rejects_missing_target():
    with pytest.raises(RuntimeError,match="no coinciden"):
        validate_predictions(cycle(),[])
