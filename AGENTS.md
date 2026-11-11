# AGENTS.md

Fork of PiBrewing/cbpi4-PIDBoil (upstream remote `upstream`), adding guards
for the boil kettle. Claude Code reads this through the `CLAUDE.md` symlink.

- `cbpi4-PIDBoil/__init__.py`: CBPi 4.7.7 kettle logic. Upstream code plus the guard wiring.
- `cbpi4-PIDBoil/guard.py`: pure guard checks. No I/O; the caller supplies time.
- `tests/test_guard.py`: `python3 -m pytest tests -q`.

Rules:
- Keep it a drop-in replacement: package dir `cbpi4-PIDBoil`, dist name
  `cbpi4-PIDBoil` and logic name `PIDBoil` stay the same, so existing kettle
  config keeps working.
- Keep upstream's code recognisable so upstream changes can still be merged.
  New logic goes in `guard.py`.
- Faults: heater off, latched until the kettle restarts, `off` re-sent every
  sample through the actor instance's `off()` (CBPi's `actor.off()` is a no-op once
  it thinks the actor is off). Never auto-resume.
- No rig-specific IDs, topics or numbers here. The operator's rig lives in
  `brewery/herms/rig.md`.
- Production CBPi (CT110) writes, installs and restarts need the operator's OK
  first. Check that nothing is brewing.
- Test in a throwaway CBPi 4.7.7 container with `cbpi4-dummy-temp` probes (see
  that repo's HOWTO.md), never on production.

Verified 2026-11-11: 7 unit tests. In a throwaway `ghcr.io/pibrewing/craftbeerpi4:v4.7.7`
container with a DummyTemp probe and a DummyActor, these three each turned the
heater off and latched it:
- over limit, with the probe set to 106 °C;
- frozen probe, with `FrozenTime` 10: it tripped at 10 s;
- no rise, with `RiseTime` 10 and the probe rising 0.5 °C/min.

A second throwaway container on the HERMS dev rig's private Mosquitto, with an
`MQTTActor` heater, checked the faulted kettle: it published `off` 6 times in
10 s (`SampleTime` 2), and `cbpi/pidboil/<id>` carried the fault reason.

Unit tests only: the boil-plateau exemption and `AppliedSensor`. Not run on production or with real
DS18B20 noise.

Gotcha found while testing: a kettle `PUT` without `target_temp` resets the target to 0.
