"""Timezone resolution: corrected area codes, priority order, duplicate-key guard."""
import ast
from pathlib import Path

from services.timezone import AREA_CODE_TO_TZ, resolve_timezone, tz_label


def test_corrected_area_codes():
    # These four were wrongly mapped to US/Central before the fix
    assert AREA_CODE_TO_TZ["717"] == "US/Eastern"   # Harrisburg PA
    assert AREA_CODE_TO_TZ["743"] == "US/Eastern"   # NC (336 overlay)
    assert AREA_CODE_TO_TZ["502"] == "US/Eastern"   # Louisville KY
    assert AREA_CODE_TO_TZ["915"] == "US/Mountain"  # El Paso TX
    # These were duplicate keys where the Mountain value silently won
    assert AREA_CODE_TO_TZ["385"] == "US/Mountain"  # Salt Lake City UT
    assert AREA_CODE_TO_TZ["720"] == "US/Mountain"  # Denver CO


def test_no_duplicate_dict_keys_in_source():
    src = (Path(__file__).resolve().parents[1] / "services" / "timezone.py").read_text()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Dict):
            keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
            dupes = {k for k in keys if keys.count(k) > 1}
            assert not dupes, f"duplicate literal dict keys: {dupes}"


def test_resolution_priority():
    # hs_timezone wins over state and phone
    assert resolve_timezone({
        "hs_timezone": "America/New_York", "state": "TX", "phone": "415-555-0100",
    }) == "US/Eastern"
    # state wins over phone
    assert resolve_timezone({"state": "TX", "phone": "212-555-0100"}) == "US/Central"
    # phone area code is the last resort
    assert resolve_timezone({"phone": "1-717-555-0100"}) == "US/Eastern"
    assert resolve_timezone({"mobilephone": "(915) 555-0100"}) == "US/Mountain"
    assert resolve_timezone({}) == "UNKNOWN"


def test_tz_label():
    assert tz_label("US/Eastern") == "ET"
    assert tz_label("UNKNOWN") == "UNKNOWN"
