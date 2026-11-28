"""Saved Volkswagen vehicle preferences."""
from opendbc.car.volkswagen.stock_aol import MQB_CARS
from openpilot.starpilot.saved_source import read_saved


def stock_long_requested(params):
  try:
    raw, readable = read_saved(params, 'AlphaLongitudinalEnabled', 8)
    return not readable or raw not in (None, b'1')
  except (OSError, TypeError, ValueError):
    return True


MUTE_CLUSTER_TAKEOVER = "VolkswagenMuteClusterTakeover"


def cluster_takeover_mute_supported(cp):
  return bool(cp is not None and cp.brand == "volkswagen" and cp.carFingerprint in MQB_CARS and
              not cp.passive and not cp.dashcamOnly and not cp.notCar)
