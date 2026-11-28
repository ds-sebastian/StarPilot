from dataclasses import replace

import pytest

from opendbc.can import CANParser
from opendbc.car import gen_empty_fingerprint, structs
from opendbc.car.volkswagen.interface import CarInterface
from opendbc.car.volkswagen.values import CAR
from openpilot.common.params import Params
from openpilot.starpilot.car.volkswagen.preferences import MUTE_CLUSTER_TAKEOVER
from openpilot.starpilot.controller_extensions import configure_controller
from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner
from openpilot.starpilot.ui.feature_settings_state import FeaturePage, FeatureSettingsRequest
from openpilot.starpilot.ui.vehicle_bool import VEHICLE_BOOL_KEYS, confirmation_question


def volkswagen(platform=CAR.VOLKSWAGEN_GOLF_MK7, *, alpha=False):
  fp = gen_empty_fingerprint()
  fp[1][0x13D if platform == CAR.VOLKSWAGEN_ID4_MK1 else 0xFD] = 8
  return CarInterface.get_params(platform, fp, [], alpha, False, False)


def test_vehicle_row_saves_only_confirmed_parked_mqb_selection(tmp_path):
  params = Params(str(tmp_path))
  cp = volkswagen()
  parked = [True]
  owner = FeatureSettingsOwner(params, lambda _: parked[0], vehicle_fingerprint=lambda: str(cp.carFingerprint),
                               vehicle_params=lambda: cp)
  def rows(page):
    return owner.snapshot(page, parked=parked[0], system_long=False, lateral_context=False, metric=True).rows

  assert params.get_default_value(MUTE_CLUSTER_TAKEOVER) is False
  assert any(row.page == FeaturePage.VEHICLE for row in rows(FeaturePage.HUB))
  row = next(row for row in rows(FeaturePage.VEHICLE) if row.key == MUTE_CLUSTER_TAKEOVER)
  assert row.value == 'Off' and row.available
  request = FeatureSettingsRequest(row.key, row.source, 'On', confirmation=True, vehicle_fingerprint=str(cp.carFingerprint),
                                   capability=row.capability)
  assert request.key in VEHICLE_BOOL_KEYS
  assert 'next startup' in confirmation_question(request)
  assert not owner.apply(replace(request, confirmation=False))
  parked[0] = False
  assert not owner.apply(request)
  parked[0] = True
  cp = volkswagen(CAR.VOLKSWAGEN_ID4_MK1)
  assert all(row.key != MUTE_CLUSTER_TAKEOVER for row in rows(FeaturePage.VEHICLE))
  assert not owner.apply(request)
  cp = volkswagen()
  assert owner.apply(request)
  assert params.get_bool(MUTE_CLUSTER_TAKEOVER)
  assert not owner.apply(replace(request, value='Off'))
  assert owner.apply(replace(request, expected=b'1', value='Off'))
  assert not params.get_bool(MUTE_CLUSTER_TAKEOVER)


@pytest.mark.parametrize('alpha', (False, True))
@pytest.mark.parametrize('visual_alert', ('steerRequired', 'ldw', 'fcw'))
@pytest.mark.parametrize('steering_pressed', (False, True))
def test_only_mqb_takeover_text_changes(tmp_path, alpha, visual_alert, steering_pressed):
  params = Params(str(tmp_path))

  def packet():
    ci = CarInterface(volkswagen(alpha=alpha))
    configure_controller(ci, params)
    ci.CS.out = structs.CarState(steeringPressed=steering_pressed).as_reader()
    ci.CS.gra_stock_values = {"COUNTER": 0}

    ci.CS.ldw_stock_values = {'LDW_SW_Warnung_links': 1, 'LDW_SW_Warnung_rechts': 1,
                             'LDW_Seite_DLCTLC': 1, 'LDW_DLC': 0.3, 'LDW_TLC': 0.4}
    cc = structs.CarControl(latActive=True)
    cc.actuators.torque = 0.1
    cc.hudControl.visualAlert = visual_alert
    cc.hudControl.leftLaneVisible = True
    cc.hudControl.rightLaneDepart = True
    _, sends = ci.CC.update(cc.as_reader(), ci.CS, 0)
    parser = CANParser('vw_mqb', [('LDW_02', 0)], ci.CC.CAN.pt)
    parser.update([1, sends])
    return dict(parser.vl['LDW_02'])

  before = packet()
  assert before['LDW_Texte'] == (0 if visual_alert == 'fcw' else 8)
  assert before['LDW_Status_LED_gruen'] == int(not steering_pressed)
  assert before['LDW_Status_LED_gelb'] == int(steering_pressed)
  assert (before['LDW_Lernmodus_links'], before['LDW_Lernmodus_rechts']) == (2, 3)
  params.put_bool(MUTE_CLUSTER_TAKEOVER, True, block=True)
  assert packet() == dict(before, LDW_Texte=0)
