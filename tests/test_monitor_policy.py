from pulso_transmi.monitor_performance import choose_decision


def test_waits_when_submission_coverage_is_low():
    assert choose_decision(.80, 92, 91, .30, 90) == "esperar"


def test_keeps_model_when_recent_six_are_good_despite_drift():
    assert choose_decision(1.0, 93, 92, .40, 90) == "conservar"


def test_retrains_on_sustained_low_accuracy():
    assert choose_decision(1.0, 84, 92, .25, 90) == "reentrenar"


def test_retrains_on_material_deterioration_with_drift():
    assert choose_decision(1.0, 90, 94, .30, 90) == "reentrenar"
