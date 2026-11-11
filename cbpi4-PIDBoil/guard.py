"""Probe and over-temperature guards for PIDBoil. Pure: no I/O, caller supplies time.

Temperatures are in CBPi's Temp Unit; the plugin converts the defaults.
Below the boil threshold the guards also catch a dead or misplaced probe. At the
boil the reading legitimately stops moving, so only the absolute checks apply.
"""
from dataclasses import dataclass
from math import isfinite

# Defaults in °C. TempMin/TempMax: plausibility, rejects CBPi's 999 and DS18B20's -127.
# MaxTemp: liquid can't get above ~100 °C, so a higher reading means a dry kettle or a
# probe in the wrong place. RiseMin is a temperature difference.
TEMPS_C = dict(TempMin=-5, TempMax=110, MaxTemp=105)
DELTAS_C = dict(RiseMin=2)
DEFAULTS = dict(MaxAge=30, FrozenTime=300, RiseTime=900, GuardPower=50)


def unit_defaults(unit):
    if unit == 'C':
        return dict(DEFAULTS, **TEMPS_C, **DELTAS_C)
    return dict(DEFAULTS, **{k: round(v * 1.8 + 32, 2) for k, v in TEMPS_C.items()},
                **{k: round(v * 1.8, 2) for k, v in DELTAS_C.items()})


@dataclass(frozen=True)
class GuardConfig:
    max_age: float      # s; 0 = no staleness check (sensor has no timestamp)
    frozen_time: float  # s at >= guard_power with an identical reading; 0 = off
    rise_time: float    # s at >= guard_power without rising rise_min; 0 = off
    rise_min: float
    guard_power: float  # %
    temp_min: float
    temp_max: float
    max_temp: float
    boil_threshold: float
    max_dt: float = 30.  # s between checks before the loop counts as stalled
    applied_time: float = 30.  # s commanded on while the panel applies 0


class Guard:
    def __init__(self, config: GuardConfig):
        self.c = config
        self.last_time = None
        self.frozen_value, self.frozen_for = None, 0.
        self.rise_from, self.rise_for = None, 0.
        self.undelivered = 0.

    def check(self, now, value, received_at, power, applied=None):
        """Reason to fault, or None. `power` is what was commanded since the last check.

        `applied`: (value, received_at) of the panel's applied power %, if a sensor is set."""
        c = self.c
        dt = 0. if self.last_time is None else now - self.last_time
        self.last_time = now
        if not 0 <= dt <= c.max_dt:
            return 'control loop stalled or clock jumped'
        if not _num(value):
            return 'kettle sensor has no reading'
        if not c.temp_min <= value <= c.temp_max:
            return f'kettle reading implausible ({value})'
        if c.max_age and not (_num(received_at) and -1 <= now - received_at <= c.max_age):
            return 'kettle reading stale'
        if value > c.max_temp:
            return f'kettle over limit ({value} > {c.max_temp}): boiled dry or probe out of the liquid?'

        if applied is not None:
            a, at = applied
            if not _num(a):
                return 'applied power sensor has no reading'
            if not -1 <= now - at <= max(c.max_age, c.max_dt):
                return 'applied power reading stale'
            # Power sharing can halve a request, so only count from 5 %.
            self.undelivered = self.undelivered + dt if power >= 5 and a <= 0 else 0.
            if c.applied_time and self.undelivered > c.applied_time:
                return (f'heater commanded {power:.0f} % but the panel applied 0 for {self.undelivered:.0f} s '
                        '(panel not obeying, or power share starving it)')

        heating = power >= c.guard_power and value < c.boil_threshold
        self.frozen_for = self.frozen_for + dt if heating and value == self.frozen_value else 0.
        self.frozen_value = value
        if c.frozen_time and self.frozen_for > c.frozen_time:
            return f'kettle reading frozen at {value} for {self.frozen_for:.0f} s with the heater on (probe dead?)'

        if not heating:
            self.rise_from, self.rise_for = None, 0.
        elif self.rise_from is None or value - self.rise_from >= c.rise_min:
            self.rise_from, self.rise_for = value, 0.  # rising: restart the window from here
        else:
            self.rise_for += dt
            if c.rise_time and self.rise_for > c.rise_time:
                return (f'heater on for {self.rise_for:.0f} s but the kettle rose less than {c.rise_min:g} '
                        f'(probe out of the liquid, element not heating?)')
        return None


def _num(v):
    return type(v) in (int, float) and isfinite(v)
