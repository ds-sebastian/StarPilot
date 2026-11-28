"""Toyota blind-spot diagnostic fallback, adapted from sunnypilot/opendbc #502.

Copyright (c) 2021-, sunnypilot, and a number of other contributors.
Licensed under the MIT License. See the repository license for the terms.
"""

from opendbc.can import CANParser
from opendbc.car import CanData, structs
from opendbc.car.toyota.values import CAR, ToyotaFlags
from opendbc.safety import ALTERNATIVE_EXPERIENCE


SIDES = (0x41, 0x42)
REPLY_MAX_AGE_NS = 500_000_000


def supported(cp):
  required = ToyotaFlags.TSS2 | ToyotaFlags.NO_DSU | ToyotaFlags.RAISED_ACCEL_LIMIT | ToyotaFlags.HYBRID
  allowed = required | ToyotaFlags.AUTO_BRAKE_HOLD
  return (cp.carFingerprint == CAR.TOYOTA_PRIUS_TSS2 and cp.brand == 'toyota' and
          cp.pcmCruise and cp.openpilotLongitudinalControl and not (cp.passive or cp.dashcamOnly or cp.notCar or cp.secOcRequired) and
          cp.steerControlType == structs.CarParams.SteerControlType.torque and cp.flags & required == required and
          not cp.flags & ~int(allowed) and len(cp.safetyConfigs) == 1 and
          cp.safetyConfigs[0].safetyModel == structs.CarParams.SafetyModel.toyota and cp.safetyConfigs[0].safetyParam == 73)


def configured(cp):
  return supported(cp) and bool(cp.alternativeExperience & ALTERNATIVE_EXPERIENCE.TOYOTA_ENHANCED_BSM)


def prepare(cp, requested):
  permission = ALTERNATIVE_EXPERIENCE.TOYOTA_ENHANCED_BSM
  cp.alternativeExperience &= ~permission
  if requested and supported(cp):
    cp.alternativeExperience |= permission


class BsmCANParser(CANParser):
  def __init__(self, *args, **kwargs):
    super().__init__(*args, **kwargs)
    self.blindspots = {}
    self.factory_bsm = False
    self.bsm_stamp = 0
    self.fresh_batch = False

  def update(self, strings, sendcan=False):
    if strings and not isinstance(strings[0], list | tuple):
      strings = [strings]
    self.fresh_batch = False
    for timestamp, frames in strings:
      self.fresh_batch = True
      if timestamp < self.bsm_stamp:
        self.blindspots.clear()
      self.bsm_stamp = timestamp
      for address, data, bus in frames:
        if bus == self.bus and address == 0x3F6 and len(data) == 8:
          self.factory_bsm = True
          self.blindspots.clear()
        if (not self.factory_bsm and bus == self.bus and address == 0x758 and len(data) == 8 and
            data[0] in SIDES and data[1:4] == b'\x06\x61\x69'):
          self.blindspots[data[0]] = (timestamp, max(data[5:7]) > 35)
    return super().update(strings, sendcan)

  def occupied(self, side):
    timestamp, present = self.blindspots.get(side, (0, False))
    return self.fresh_batch and present and 0 < timestamp <= self.bsm_stamp <= timestamp + REPLY_MAX_AGE_NS


class BsmPoller:
  def __init__(self):
    self.active = False
    self.started = 0

  def update(self, state, frame, *, factory_bsm=False):
    ready = (not factory_bsm and frame > 200 and state.canValid and not state.canTimeout and
             state.gearShifter in (structs.CarState.GearShifter.drive, structs.CarState.GearShifter.sport) and
             state.vEgo > (6.0 if self.active else 7.0))
    if ready != self.active:
      self.active = ready
      self.started = frame
      session = 0x60 if ready else 0x01
      return [CanData(0x750, bytes((side, 2, 0x10, session, 0, 0, 0, 0)), 0) for side in SIDES]
    # Retry twice if Panda had not yet admitted the initial session request.
    # Panda ignores the repeated entry command on a side with an open session.
    if ready and frame - self.started in (100, 200):
      return [CanData(0x750, bytes((side, 2, 0x10, 0x60, 0, 0, 0, 0)), 0) for side in SIDES]
    if ready and frame % 10 == 5:
      side = SIDES[(frame // 10) % 2]
      return [CanData(0x750, bytes((side, 2, 0x21, 0x69, 0, 0, 0, 0)), 0)]
    return []
