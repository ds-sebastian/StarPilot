from dataclasses import replace
from types import SimpleNamespace

from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.toyota.enhanced_bsm import configured
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, row_change
from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question
from openpilot.starpilot.vehicle_preferences import VehicleStartupPreferences


def test_vehicle_setting_and_startup_permission(tmp_path):
  params = Params(str(tmp_path))
  fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)]
  current = SimpleNamespace(cp=CarInterface.get_params(CAR.TOYOTA_PRIUS_TSS2, gen_empty_fingerprint(), fw, False, False, False), parked=True)
  owner = FeatureSettingsOwner(params, lambda group: current.parked and group in ('vehicle', 'parked_preferences'),
                               vehicle_fingerprint=lambda: current.cp.carFingerprint, vehicle_params=lambda: current.cp)

  def rows():
    state = owner.snapshot(FeaturePage.VEHICLE, parked=current.parked, system_long=True, lateral_context=True, metric=False)
    return [row for row in state.rows if row.key == 'ToyotaEnhancedBsm']

  assert not configured(VehicleStartupPreferences.read(params, enabled=True).prepare(current.cp))
  row, = rows()
  assert row.available and row.value == 'Off'
  request = row_change(row)
  assert request.key in VEHICLE_BOOL_KEYS and 'next startup' in confirmation_question(request)
  assert not owner.apply(request)
  request = replace(request, confirmation=True)
  current.parked = False
  assert not owner.apply(request)
  current.parked = True
  assert owner.apply(request)
  assert not owner.apply(request)
  prefs = VehicleStartupPreferences.read(params, enabled=True)
  cp = prefs.prepare(current.cp)
  assert configured(cp)
  cp.alternativeExperience = 0  # Card resets this before finalizing startup preferences.
  prefs.finalize(cp)
  assert configured(cp)

  params.put_bool('SafeMode', True, block=True)
  VehicleStartupPreferences.read(params, enabled=True).finalize(cp)
  assert not configured(cp)
  fp = gen_empty_fingerprint()
  fp[0][0x3F6] = 8
  current.cp = CarInterface.get_params(CAR.TOYOTA_PRIUS_TSS2, fp, fw, False, False, False)
  assert not rows()
  assert not configured(prefs.prepare(current.cp))
