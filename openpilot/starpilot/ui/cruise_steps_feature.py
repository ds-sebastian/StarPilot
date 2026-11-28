from __future__ import annotations

import math
from typing import TYPE_CHECKING

from openpilot.starpilot.controllers.toyota_cruise import capability as toyota_cruise_capability
from openpilot.starpilot.saved_document import commit_exact
from openpilot.starpilot.saved_source import read_saved
from openpilot.starpilot.ui.feature_settings_state import FeatureRow, FeatureSettingsRequest

if TYPE_CHECKING:
  from openpilot.starpilot.ui.feature_settings_owner import FeatureSettingsOwner


CRUISE_KEYS = frozenset(("CustomCruise", "CustomCruiseLong"))


def cruise_rows(owner: FeatureSettingsOwner, configurable: bool) -> list[FeatureRow]:
  software_capability = owner._cruise_capability()
  toyota_capability = toyota_cruise_capability(owner.vehicle_params())
  capability = software_capability or toyota_capability
  group = "toyota_cruise" if toyota_capability is not None else "long"
  allowed = configurable and capability is not None and owner.authority(group)
  unit_value, unit_raw, unit_valid = owner._value("IsMetric")
  unit_valid = unit_valid and unit_value in ("0", "1") and owner._readable("IsMetric")
  unit = "km/h" if unit_value == "1" else "mph"
  reason = "Requires StarPilot cruise speed control" if capability is None else "Requires a compatible cruise configuration"
  rows = []
  if toyota_capability is not None:
    value, raw, valid = owner._value("ReverseCruise")
    rows.append(FeatureRow("ReverseCruise", "Swap Toyota Cruise Steps", "On" if value == "1" else "Off" if value == "0" else "Invalid saved value",
                           raw, ("Off", "On") if valid else (), available=allowed and valid,
                           reason="Short press changes set speed by 5; hold changes it by 1",
                           capability=toyota_capability, dependencies=()))
    return rows
  for key, label, default in (("CustomCruise", "Short press", 1.0), ("CustomCruiseLong", "Hold", 5.0)):
    value, raw, valid = owner._value(key)
    try:
      number = float(value)
      valid = valid and owner._readable(key) and math.isfinite(number) and 1.0 <= number <= 150.0
    except ValueError:
      valid = False
      number = default
    rows.append(FeatureRow(key, label, f"{number:g}" if valid else "Invalid saved value", raw,
                           step=1.0 if valid else 0.0, minimum=1.0, maximum=150.0 if unit_value == "1" else 99.0,
                           unit=unit if unit_valid else "", available=allowed and valid and unit_valid,
                           reason="Invalid saved value or units" if not valid or not unit_valid else
                                  "Used when StarPilot controls cruise speed" if allowed else reason,
                           capability=capability, dependencies=(("IsMetric", unit_raw),),
                           display_unit=unit if unit_valid else "", default_value=str(default)))
  return rows

def apply_cruise(owner: FeatureSettingsOwner, request: FeatureSettingsRequest) -> bool:
  key = request.key
  if key not in CRUISE_KEYS or not request.confirmation:
    return False
  def authorized() -> bool:
    if (not request.vehicle_fingerprint or owner.vehicle_fingerprint() != request.vehicle_fingerprint or
        request.capability is None or request.capability != (owner._cruise_capability() or toyota_cruise_capability(owner.vehicle_params())) or
        not owner.authority("long")):
      return False
    raw, readable = read_saved(owner.params, key, 128)
    if not readable or raw != request.expected:
      return False
    if owner._cruise_capability() is None:
      return False
    units, units_readable = read_saved(owner.params, "IsMetric", 8)
    return (units_readable and units in (None, b"0", b"1") and
            request.dependencies == (("IsMetric", units),) and
            request.display_unit == ("km/h" if units == b"1" else "mph"))
  if not authorized():
    return False
  try:
    number = float(request.value)
  except ValueError:
    return False
  upper = 150.0 if request.display_unit == "km/h" else 99.0
  if not math.isfinite(number) or not 1.0 <= number <= upper:
    return False
  encoded = str(number).encode()
  return commit_exact(owner.params, key=key, max_bytes=128, raw=encoded, expected=request.expected,
                      authorized=authorized, temp_prefix=".cruise-interval-").verified

def apply_reverse_cruise(owner: FeatureSettingsOwner, request: FeatureSettingsRequest) -> bool:
  if request.value not in ("On", "Off"):
    return False
  def authorized():
    cap = toyota_cruise_capability(owner.vehicle_params())
    return bool(cap is not None and request.capability == cap and owner.authority("toyota_cruise") and
                request.vehicle_fingerprint == owner.vehicle_fingerprint() and request.dependencies == ())
  return commit_exact(owner.params, key="ReverseCruise", max_bytes=8, expected=request.expected,
                      raw=b"1" if request.value == "On" else b"0", authorized=authorized,
                      temp_prefix=".toyota-cruise-").verified
