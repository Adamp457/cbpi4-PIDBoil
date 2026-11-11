import asyncio
import logging
from cbpi.api import *
from cbpi.api.dataclasses import NotificationType
import time

from .guard import Guard, GuardConfig, unit_defaults


def _help(key, text):
    """Description with the default in both units: the form can't know CBPi's Temp Unit."""
    return f"{text}. Empty = {unit_defaults('C')[key]:g} (°C) / {unit_defaults('F')[key]:g} (°F)"


@parameters([Property.Number(label = "P", configurable = True, description="P Value of PID"),
             Property.Number(label = "I", configurable = True, description="I Value of PID"),
             Property.Number(label = "D", configurable = True, description="D Value of PID"),
             Property.Select(label="SampleTime", options=[2,5], description="PID Sample time in seconds. Default: 5 (How often is the output calculation done)"),
             Property.Number(label = "Max_Output", configurable = True, description="Power before Boil threshold is reached."),
             Property.Number(label = "Boil_Threshold", configurable = True, description="When this temperature is reached, power will be set to Max Boil Output (default: 98 °C/208 F)"),
             Property.Number(label = "Max_Boil_Output", configurable = True, default_value = 85, description="Power when Boil Threshold is reached."),
             # Guards (fork). Any fault turns the heater off and latches until the kettle is restarted.
             Property.Number(label = "MaxTemp", configurable = True, description=_help("MaxTemp", "Fault above this kettle temperature (boiled dry, probe out of the liquid)")),
             Property.Number(label = "MaxAge", configurable = True, description="Fault if the probe has not updated for this long, s (MQTT sensors need Timeout > 0). 0 = off. Empty = 30"),
             Property.Number(label = "FrozenTime", configurable = True, description="Below the boil threshold: fault if the probe reads exactly the same for this long with the heater at 50 % or more, s. 0 = off. Empty = 300"),
             Property.Number(label = "RiseTime", configurable = True, description=_help("RiseMin", "Below the boil threshold: fault if the heater is at 50 % or more for RiseTime s (empty = 900, 0 = off) and the kettle rises less than this")),
             Property.Sensor(label = "AppliedSensor", description="Optional: the panel's applied heater power in % (MQTTDevice32pIO power-share <topic>/applied). Fault if the heater is commanded on and the panel applies 0 for 30 s")])

class PIDBoil(CBPiKettleLogic):

    def _num(self, key, defaults):
        value = self.props.get(key)
        return float(defaults[key] if value in (None, "") else value)

    def _guard(self, unit, boil_threshold):
        d = unit_defaults(unit)
        return Guard(GuardConfig(
            max_age=self._num("MaxAge", d), frozen_time=self._num("FrozenTime", d),
            rise_time=self._num("RiseTime", d), rise_min=d["RiseMin"], guard_power=d["GuardPower"],
            temp_min=d["TempMin"], temp_max=d["TempMax"], max_temp=self._num("MaxTemp", d),
            boil_threshold=boil_threshold))

    def _reading(self, now):
        try:
            value = float(self.get_sensor_value(self.kettle.sensor).get("value"))
        except (AttributeError, TypeError, ValueError):
            return None, now
        sensor = self.cbpi.sensor.find_by_id(self.kettle.sensor)
        # MQTTSensor stamps lastdata per message (only when its Timeout > 0). Sensors
        # without it skip the age check; the frozen-reading guard covers them.
        return value, getattr(getattr(sensor, "instance", None), "lastdata", now)

    def _applied(self, now):
        sensor_id = self.props.get("AppliedSensor")
        if not sensor_id:
            return None
        try:
            value = float(self.get_sensor_value(sensor_id).get("value"))
        except (AttributeError, TypeError, ValueError):
            return None, now
        sensor = self.cbpi.sensor.find_by_id(sensor_id)
        return value, getattr(getattr(sensor, "instance", None), "lastdata", now)

    async def _force_off(self):
        # CBPi's actor.off() does nothing once it thinks the actor is off, and MQTTActor marks
        # itself off before publishing. Calling the actor's own off() again re-publishes it.
        if self.get_actor_state(self.heater):
            return await self.actor_off(self.heater)
        actor = self.cbpi.actor.find_by_id(self.heater)
        if actor is not None and actor.instance is not None:
            try:
                await actor.instance.off()
            except Exception:
                logging.warning("PIDBoil %s: could not re-send heater off", self.id)

    def _status(self, state, power, reason, temp):
        self.cbpi.push_update(f"cbpi/pidboil/{self.id}", dict(
            state=state, power=round(power), reason=reason, temp=temp))

    async def on_stop(self):
        await self.actor_off(self.heater)
        pass

    async def run(self):
        try:
            self.TEMP_UNIT = self.get_config_value("TEMP_UNIT", "C")
            wait_time = sampleTime = int(self.props.get("SampleTime",5))
            boilthreshold = 98 if self.TEMP_UNIT == "C" else 208

            p = float(self.props.get("P", 117.0795))
            i = float(self.props.get("I", 0.2747))
            d = float(self.props.get("D", 41.58))
            maxout = int(self.props.get("Max_Output", 100))
            maxtempboil = float(self.props.get("Boil_Threshold", boilthreshold))
            maxboilout = int(self.props.get("Max_Boil_Output", 100))
            self.kettle = self.get_kettle(self.id)
            self.heater = self.kettle.heater
            heat_percent_old = 0
            self.heater_actor = self.cbpi.actor.find_by_id(self.heater)
            guard = self._guard(self.TEMP_UNIT, maxtempboil)
            fault = None

            pid = PIDArduino(sampleTime, p, i, d, 0, maxout)

            while self.running == True:
                now = time.time()
                current_temp, received_at = self._reading(now)
                if fault is None and (fault := guard.check(now, current_temp, received_at, heat_percent_old, self._applied(now))):
                    logging.warning("PIDBoil %s fault: %s", self.id, fault)
                    self.cbpi.notify("PIDBoil", f"{self.kettle.name}: heater off: {fault}. "
                                     "Restart the kettle to re-arm.", NotificationType.ERROR)
                if fault:
                    # Re-sent every sample, so a lost off can't leave the element on.
                    await self._force_off()
                    heat_percent_old = 0
                    self._status("fault", 0, fault, current_temp)
                    await asyncio.sleep(sampleTime)
                    continue

                target_temp = self.get_kettle_target_temp(self.id)
                if current_temp >= float(maxtempboil):
                    heat_percent = maxboilout
                else:
                    heat_percent = pid.calc(current_temp, target_temp)

                # Upstream switched on at Max_Output before reading the probe; now on only after the first good check.
                if not self.get_actor_state(self.heater):
                    await self.actor_on(self.heater, heat_percent)
                elif (heat_percent_old != heat_percent) or (heat_percent != self.heater_actor.power):
                    await self.actor_set_power(self.heater, heat_percent)
                heat_percent_old = heat_percent
                self._status("running", heat_percent, None, current_temp)
                await asyncio.sleep(sampleTime)

        except asyncio.CancelledError as e:
            pass
        except Exception as e:
            logging.exception("PIDBoil Error {}".format(e))
        finally:
            self.running = False
            await self.actor_off(self.heater)

# Based on Arduino PID Library
# See https://github.com/br3ttb/Arduino-PID-Library
class PIDArduino(object):

    def __init__(self, sampleTimeSec, kp, ki, kd, outputMin=float('-inf'),
                 outputMax=float('inf'), getTimeMs=None):
        if kp is None:
            raise ValueError('kp must be specified')
        if ki is None:
            raise ValueError('ki must be specified')
        if kd is None:
            raise ValueError('kd must be specified')
        if float(sampleTimeSec) <= float(0):
            raise ValueError('sampleTimeSec must be greater than 0')
        if outputMin >= outputMax:
            raise ValueError('outputMin must be less than outputMax')

        self._logger = logging.getLogger(type(self).__name__)
        self._Kp = kp
        self._Ki = ki * sampleTimeSec
        self._Kd = kd / sampleTimeSec
        self._sampleTime = sampleTimeSec * 1000
        self._outputMin = outputMin
        self._outputMax = outputMax
        self._iTerm = 0
        self._lastInput = 0
        self._lastOutput = 0
        self._lastCalc = 0

        if getTimeMs is None:
            self._getTimeMs = self._currentTimeMs
        else:
            self._getTimeMs = getTimeMs

    def calc(self, inputValue, setpoint):
        now = self._getTimeMs()

        if (now - self._lastCalc) < self._sampleTime:
            return self._lastOutput

        # Compute all the working error variables
        error = setpoint - inputValue
        dInput = inputValue - self._lastInput

        # In order to prevent windup, only integrate if the process is not saturated
        if self._lastOutput < self._outputMax and self._lastOutput > self._outputMin:
            self._iTerm += self._Ki * error
            self._iTerm = min(self._iTerm, self._outputMax)
            self._iTerm = max(self._iTerm, self._outputMin)

        p = self._Kp * error
        i = self._iTerm
        d = -(self._Kd * dInput)

        # Compute PID Output
        self._lastOutput = p + i + d
        self._lastOutput = min(self._lastOutput, self._outputMax)
        self._lastOutput = max(self._lastOutput, self._outputMin)

        # Log some debug info
        self._logger.debug('P: {0}'.format(p))
        self._logger.debug('I: {0}'.format(i))
        self._logger.debug('D: {0}'.format(d))
        self._logger.debug('output: {0}'.format(self._lastOutput))

        # Remember some variables for next time
        self._lastInput = inputValue
        self._lastCalc = now
        return self._lastOutput

    def _currentTimeMs(self):
        return time.time() * 1000

def setup(cbpi):

    '''
    This method is called by the server during startup 
    Here you need to register your plugins at the server
    
    :param cbpi: the cbpi core 
    :return: 
    '''

    cbpi.plugin.register("PIDBoil", PIDBoil)
