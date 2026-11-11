# Craftbeerpi4 Kettle Logic Plugin

## PID Logic with boil threshold.

If the target Temperature is above a configurable threshold the PID will be ignored and heater is switched on to the max output value. This is helpful if you use the same kettle for mashing and boiling.

Once the boil threshold temperature is reached, the boil will be done with the max boil output power


## Fork: probe and over-temperature guards (0.1.0)

This fork of [PiBrewing/cbpi4-PIDBoil](https://github.com/PiBrewing/cbpi4-PIDBoil)
adds guards. It's a drop-in replacement: same package name, same logic name
`PIDBoil`, and existing kettles keep their settings.

On any fault the heater goes off, a CBPi notification says why, and the fault
**latches** until you restart the kettle. While faulted, the `off` is re-sent
every sample by calling the actor's own `off()`, so a lost MQTT message
can't leave the element on. (CBPi's `actor.off()` does nothing once CBPi thinks
the actor is off, so calling that alone would not retry.)

| Guard | Faults when | Property (empty = default) |
|---|---|---|
| No reading / implausible | Probe gives no number, or reads outside -5 to 110 °C (catches CBPi's `999` and DS18B20's `-127`) | — |
| Stale | Probe hasn't updated for `MaxAge` s. MQTT sensors need `Timeout` > 0, or they always look stale | `MaxAge` 30, 0 = off |
| Over limit | Kettle above `MaxTemp`. Liquid can't go much past 100 °C, so this means boiled dry or the probe is out of the liquid | `MaxTemp` 105 °C / 221 °F |
| Frozen probe | Below the boil threshold, the reading stays exactly the same for `FrozenTime` s with the heater at ≥ 50 % | `FrozenTime` 300, 0 = off |
| No rise | Below the boil threshold, heater at ≥ 50 % for `RiseTime` s and the kettle rose less than 2 °C (3.6 °F) | `RiseTime` 900, 0 = off |
| Loop stalled | More than 30 s between samples | — |
| Undelivered power | With `AppliedSensor` set (MQTTDevice32pIO power-share `<heater topic>/applied`, key `apw`, `Timeout` > 0): heater commanded at ≥ 5 % and the panel applies 0 for 30 s, or that reading is missing or stale | `AppliedSensor`, empty = off |

At the boil a still reading is normal, so the frozen and no-rise checks only run
below `Boil_Threshold`. Only the over-limit check covers a boil-dry.

Other changes from upstream:
- The heater switches on only after the first good reading. Upstream switched it on at `Max_Output` before reading the probe.
- Each sample is published to `cbpi/pidboil/<kettle id>` as `{state, power, reason, temp}`, with `state` `running` or `fault`, for Home Assistant alerts.

**This is not a safety system.** Fit a hardwired over-temperature cutout and
dry-fire protection on the element. These guards are a fallback for when the
software or a probe fails.

Install: `pip install https://github.com/Adamp457/cbpi4-PIDBoil/archive/main.zip`,
then restart CBPi. Tests: `python3 -m pytest tests -q`.

### Installation (upstream):

Please have a look at the [Craftbeerpi4 Documentation](https://openbrewing.gitbook.io/craftbeerpi4_support/readme/plugin-installation)

- Package name: cbpi4-PIDBoil
- Package link: https://github.com/PiBrewing/cbpi4-pidboil/archive/main.zip


### Parameters

![PIDBoil Settings](https://github.com/avollkopf/cbpi4-PIDBoil/blob/main/cbpi4-PIDBoil-logic.png?raw=true)

- P - proportional value
- I - integral value
- D - derivative value
- SampleTime - 2 or 5 seconds -> how often the logic calculates the power setting
- max output - heater power which is set above boil threshold
- Boil Threshold - Above this temperature the heater will be set to Max Boil Output Power (default: 98°C / 208F)
- Max Boil Output - Power (%) that is used above Boil Threshold Temperature (default: 100%)

### Changelog

- 11.11.26: (0.1.0, fork) Probe, stale, frozen, no-rise and over-temperature guards with a latched fault; heater on only after the first good reading; status topic

- 29.12.22: (0.0.9) Fixed Bug related to Boil Threshold Parameter
- 10.05.22: (0.0.8) Updated README (removed cbpi add)
- 10.05.22: (0.0.7) Removed cbpi dependency
- 01.12.21: (0.0.6) If current kettle power is different from kettle logic. logic will overrule kettle power setting
- 21.11.21: (0.0.5) Adapted to cbpi4 4.0.0.45 to accomodate actor power settings incl. the PWM Actor.
- 13.10.21: (0.0.4) Added Power setting for Boil
- 13.10.21: (0.0.3) Improvement of actor toggling in case of 0% or 100% heating
- 12.10.21: (0.0.2) Bug fixing MashStep Automode Issue
- 19.08.21: (0.0.1) Initial commit
