import pytest

from opendbc.car import Bus, gen_empty_fingerprint, structs
from opendbc.car.toyota import enhanced_bsm
from opendbc.car.toyota.interface import CarInterface
from opendbc.car.toyota.values import CAR, DBC
from opendbc.safety import ALTERNATIVE_EXPERIENCE as AE
from opendbc.can import CANPacker
from opendbc.safety.tests.libsafety import libsafety_py


def params(car=CAR.TOYOTA_PRIUS_TSS2, *, factory=False, requested=True):
  fingerprint = gen_empty_fingerprint()
  if factory:
    fingerprint[0][0x3F6] = 8
  fw = [structs.CarParams.CarFw(ecu=structs.CarParams.Ecu.hybrid)]
  cp = CarInterface.get_params(car, fingerprint, fw, False, False, False)
  enhanced_bsm.prepare(cp, requested)
  return cp


@pytest.mark.parametrize('car,factory,requested,configured', (
  (CAR.TOYOTA_PRIUS_TSS2, False, True, True),
  (CAR.TOYOTA_PRIUS_TSS2, True, True, False),
  (CAR.TOYOTA_PRIUS_TSS2, False, False, False),
  (CAR.TOYOTA_COROLLA_TSS2, False, True, False),
))
def test_fallback_admission(car, factory, requested, configured):
  cp = params(car, factory=factory, requested=requested)
  ci = CarInterface(cp)
  assert isinstance(ci.can_parsers[Bus.pt], enhanced_bsm.BsmCANParser) == configured
  if factory:
    packer = CANPacker(DBC[car][Bus.pt])
    bsm = packer.make_can_msg('BSM', 0, {'L_APPROACHING': 1})
    ci.update([(990_000_000, [])])  # The factory parser registers BSM on first access.
    assert ci.update([(1_000_000_000, [bsm])]).leftBlindspot
  assert (ci.CC.bsm_poller is not None) == configured


def test_fresh_bsm_replies_are_independent_and_use_recorded_idle_format():
  cp = params()
  parser = CarInterface(cp).can_parsers[Bus.pt]
  left = (0x758, bytes.fromhex('4106616900320089'), 0)
  right = (0x758, bytes.fromhex('4206616900000089'), 0)
  parser.update([(1_000_000_000, [left, right])])
  assert parser.occupied(0x41)
  assert not parser.occupied(0x42)
  parser.update([(1_300_000_000, [(0x758, bytes.fromhex('4206616900310089'), 0)])])
  parser.update([(1_500_000_001, [(*left[:2], 2), (*left[:2], 128), (0x758, b'\x41\x06\x50\x69\x00\x39\x39\x89', 0)])])
  assert not parser.occupied(0x41)
  assert parser.occupied(0x42)
  parser.update([(1_450_000_000, [])])
  assert not parser.occupied(0x42)


def test_late_factory_broadcast_takes_priority():
  cp = params()
  ci = CarInterface(cp)
  packer = CANPacker(DBC[CAR.TOYOTA_PRIUS_TSS2][Bus.pt])
  factory = packer.make_can_msg('BSM', 0, {'L_APPROACHING': 1})
  right = (0x758, bytes.fromhex('4206616900390089'), 0)
  state = ci.update([(1_000_000_000, [factory, right])])
  assert state.leftBlindspot and not state.rightBlindspot
  assert ci.CS.factory_bsm


def test_poll_cadence_and_cleanup():
  poller = enhanced_bsm.BsmPoller()
  cs = structs.CarState(vEgo=9, canValid=True, gearShifter=structs.CarState.GearShifter.drive)
  assert not poller.update(cs, 200)
  assert [m.dat[:4] for m in poller.update(cs, 201)] == [b'A\x02\x10\x60', b'B\x02\x10\x60']
  assert [m.dat[:4] for m in poller.update(cs, 205)] == [b'A\x02\x21\x69']
  assert [m.dat[:4] for m in poller.update(cs, 215)] == [b'B\x02\x21\x69']
  cs.canTimeout = True
  assert [m.dat[:4] for m in poller.update(cs, 216)] == [b'A\x02\x10\x01', b'B\x02\x10\x01']
  assert not poller.update(cs, 217)


@pytest.mark.parametrize('release', (False, True))
def test_native_diagnostics_are_scoped_and_require_a_paced_moving_session(tmp_path, release):
  path = libsafety_py._build_libsafety(release=release, output_dir=tmp_path)
  safety = libsafety_py.ffi.dlopen(path)
  packer = CANPacker('toyota_nodsu_pt_generated')

  def rx(name, values):
    a, data, b = packer.make_can_msg(name, 0, values)
    assert safety.safety_rx_hook(libsafety_py.make_CANPacket(a, b, data))

  def wheel(speed):
    rx('WHEEL_SPEEDS', {f'WHEEL_SPEED_{wheel}': speed * 3.6 for wheel in ('FL', 'FR', 'RL', 'RR')})

  def start(word, permission, speed=8.):
    safety.init_tests()
    safety.set_alternative_experience(permission)
    assert safety.set_safety_hooks(2, word) == 0
    safety.set_timer(1_000_000)
    wheel(speed)
    rx('STEER_TORQUE_SENSOR', {})
    rx('BRAKE_MODULE', {})
    rx('PCM_CRUISE', {'GAS_RELEASED': True})

  def tx(data, bus=0):
    return safety.safety_tx_hook(libsafety_py.make_CANPacket(0x750, bus, data))

  enable = bytes.fromhex('4102106000000000')
  poll = bytes.fromhex('4102216900000000')
  end = bytes.fromhex('4102100100000000')
  radar = bytes.fromhex('0f023e0000000000')
  for word, feature in ((73, 0), (585, AE.TOYOTA_ENHANCED_BSM), (0x1049, AE.TOYOTA_ENHANCED_BSM)):
    start(word, feature)
    assert not tx(enable)
    assert not tx(poll)
    assert not tx(end)
  start(73, AE.TOYOTA_ENHANCED_BSM, 0.)
  assert not tx(enable)
  wheel(8.)
  assert not tx(poll)
  assert not tx(enable, 2)
  assert not tx(enable[:-1])
  assert not tx(enable[:-1] + b'\x01')
  assert tx(radar)
  assert tx(enable)
  assert not tx(enable)
  assert not tx(poll)
  safety.set_timer(1_040_000)
  assert tx(poll)
  assert not tx(poll)
  safety.set_timer(1_101_000)
  assert not tx(poll)
  wheel(0.)
  assert not tx(poll)
  assert tx(end)
  assert not tx(end)
  wheel(8.)
  assert tx(enable)
  rx('BSM', {})
  assert not tx(poll)
  assert tx(end)
  assert not tx(enable)
  assert not safety.get_controls_allowed()
