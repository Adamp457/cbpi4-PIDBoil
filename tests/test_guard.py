"""Guard checks. Run: python3 -m pytest tests -q"""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('guard', Path(__file__).parents[1] / 'cbpi4-PIDBoil' / 'guard.py')
g = importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

CFG = g.GuardConfig(max_age=30, frozen_time=300, rise_time=900, rise_min=2, guard_power=50,
                    temp_min=-5, temp_max=110, max_temp=105, boil_threshold=98, max_dt=30)


def run(values, power=100, step=5, cfg=CFG):
    """Feed one reading per `step` s; return (time, reason) of the first fault or None."""
    guard = g.Guard(cfg)
    for n, v in enumerate(values):
        t = n * step
        if reason := guard.check(t, v, t, power):
            return t, reason
    return None


def test_healthy_heat_up_and_boil():
    ramp = [20 + 0.05 * n for n in range(1600)]  # 0.6 °C/min up to 100
    assert run([min(v, 99.6) for v in ramp] + [99.6] * 600) is None  # long steady boil is fine


def test_bad_readings():
    for v, word in ((None, 'no reading'), (999, 'implausible'), (-127, 'implausible'), (106, 'over limit')):
        assert word in run([v])[1]
    assert 'stale' in g.Guard(CFG).check(100, 50, 60, 100)
    assert 'stale' in g.Guard(CFG).check(100, 50, None, 100)
    no_ts = g.GuardConfig(**{**vars(CFG), 'max_age': 0})
    assert g.Guard(no_ts).check(100, 50, None, 100) is None


def test_frozen_probe_below_boil_only():
    t, reason = run([60.0] * 100)
    assert 'frozen' in reason and 300 < t <= 310
    assert run([60.0] * 100, power=20) is None  # heater not working hard
    assert run([99.0] * 100) is None  # at the boil a still reading is normal


def test_no_rise():
    wobble = [60 + (n % 2) * 0.1 for n in range(400)]  # moves, so not frozen, but no rise
    t, reason = run(wobble)
    assert 'rose less' in reason and 900 < t <= 910
    assert run(wobble, power=20) is None


def test_stall():
    guard = g.Guard(CFG)
    assert guard.check(0, 50, 0, 100) is None
    assert 'stalled' in guard.check(60, 50, 60, 100)


def test_fahrenheit_defaults():
    f = g.unit_defaults('F')
    assert (f['MaxTemp'], f['TempMin'], f['RiseMin'], f['FrozenTime']) == (221, 23, 3.6, 300)


def test_undelivered_power():
    def go(applied_value, power=100):
        guard = g.Guard(CFG)
        for n in range(20):
            t = n * 2.
            if reason := guard.check(t, 50 + n * 0.5, t, power, (applied_value, t)):
                return t, reason
    t, reason = go(0.)
    assert 'panel applied 0' in reason and 30 < t <= 32
    assert go(40.) is None
    assert go(0., power=3) is None  # small requests may be scaled to nothing
    assert 'no reading' in g.Guard(CFG).check(0, 50, 0, 100, (None, 0))
    assert 'stale' in g.Guard(CFG).check(100, 50, 100, 100, (0., 0))
