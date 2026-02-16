"""
Timezone resolution — STATE_TO_TZ, AREA_CODE_TO_TZ, resolve_timezone, tz_label.

Priority: hs_timezone (HubSpot) > state abbreviation/name > phone area code.
"""
import re


STATE_TO_TZ = {
    # Eastern
    "CT": "US/Eastern", "DC": "US/Eastern", "DE": "US/Eastern", "FL": "US/Eastern",
    "GA": "US/Eastern", "IN": "US/Eastern", "MA": "US/Eastern", "MD": "US/Eastern",
    "ME": "US/Eastern", "MI": "US/Eastern", "NC": "US/Eastern", "NH": "US/Eastern",
    "NJ": "US/Eastern", "NY": "US/Eastern", "OH": "US/Eastern", "PA": "US/Eastern",
    "RI": "US/Eastern", "SC": "US/Eastern", "VA": "US/Eastern", "VT": "US/Eastern",
    "WV": "US/Eastern",
    "CONNECTICUT": "US/Eastern", "DISTRICT OF COLUMBIA": "US/Eastern",
    "DELAWARE": "US/Eastern", "FLORIDA": "US/Eastern", "GEORGIA": "US/Eastern",
    "INDIANA": "US/Eastern", "MASSACHUSETTS": "US/Eastern", "MARYLAND": "US/Eastern",
    "MAINE": "US/Eastern", "MICHIGAN": "US/Eastern", "NORTH CAROLINA": "US/Eastern",
    "NEW HAMPSHIRE": "US/Eastern", "NEW JERSEY": "US/Eastern", "NEW YORK": "US/Eastern",
    "OHIO": "US/Eastern", "PENNSYLVANIA": "US/Eastern", "RHODE ISLAND": "US/Eastern",
    "SOUTH CAROLINA": "US/Eastern", "VIRGINIA": "US/Eastern", "VERMONT": "US/Eastern",
    "WEST VIRGINIA": "US/Eastern",
    # Central
    "AL": "US/Central", "AR": "US/Central", "IA": "US/Central", "IL": "US/Central",
    "KS": "US/Central", "KY": "US/Central", "LA": "US/Central", "MN": "US/Central",
    "MO": "US/Central", "MS": "US/Central", "ND": "US/Central", "NE": "US/Central",
    "OK": "US/Central", "SD": "US/Central", "TN": "US/Central", "TX": "US/Central",
    "WI": "US/Central",
    "ALABAMA": "US/Central", "ARKANSAS": "US/Central", "IOWA": "US/Central",
    "ILLINOIS": "US/Central", "KANSAS": "US/Central", "KENTUCKY": "US/Central",
    "LOUISIANA": "US/Central", "MINNESOTA": "US/Central", "MISSOURI": "US/Central",
    "MISSISSIPPI": "US/Central", "NORTH DAKOTA": "US/Central", "NEBRASKA": "US/Central",
    "OKLAHOMA": "US/Central", "SOUTH DAKOTA": "US/Central", "TENNESSEE": "US/Central",
    "TEXAS": "US/Central", "WISCONSIN": "US/Central",
    # Mountain
    "AZ": "US/Mountain", "CO": "US/Mountain", "ID": "US/Mountain", "MT": "US/Mountain",
    "NM": "US/Mountain", "UT": "US/Mountain", "WY": "US/Mountain",
    "ARIZONA": "US/Mountain", "COLORADO": "US/Mountain", "IDAHO": "US/Mountain",
    "MONTANA": "US/Mountain", "NEW MEXICO": "US/Mountain", "UTAH": "US/Mountain",
    "WYOMING": "US/Mountain",
    # Pacific
    "CA": "US/Pacific", "NV": "US/Pacific", "OR": "US/Pacific", "WA": "US/Pacific",
    "HI": "US/Hawaii",
    "CALIFORNIA": "US/Pacific", "NEVADA": "US/Pacific", "OREGON": "US/Pacific",
    "WASHINGTON": "US/Pacific", "HAWAII": "US/Hawaii",
    # Alaska
    "AK": "US/Alaska", "ALASKA": "US/Alaska",
}

AREA_CODE_TO_TZ = {
    # Eastern
    "201": "US/Eastern", "202": "US/Eastern", "203": "US/Eastern", "207": "US/Eastern",
    "212": "US/Eastern", "215": "US/Eastern", "216": "US/Eastern", "239": "US/Eastern",
    "240": "US/Eastern", "248": "US/Eastern", "267": "US/Eastern", "301": "US/Eastern",
    "302": "US/Eastern", "305": "US/Eastern", "313": "US/Eastern", "315": "US/Eastern",
    "321": "US/Eastern", "336": "US/Eastern", "347": "US/Eastern", "352": "US/Eastern",
    "386": "US/Eastern", "401": "US/Eastern", "404": "US/Eastern", "407": "US/Eastern",
    "410": "US/Eastern", "412": "US/Eastern", "413": "US/Eastern", "434": "US/Eastern",
    "440": "US/Eastern", "443": "US/Eastern", "484": "US/Eastern", "508": "US/Eastern",
    "513": "US/Eastern", "516": "US/Eastern", "518": "US/Eastern", "540": "US/Eastern",
    "551": "US/Eastern", "561": "US/Eastern", "570": "US/Eastern", "571": "US/Eastern",
    "585": "US/Eastern", "586": "US/Eastern", "603": "US/Eastern", "609": "US/Eastern",
    "610": "US/Eastern", "614": "US/Eastern", "617": "US/Eastern", "631": "US/Eastern",
    "646": "US/Eastern", "678": "US/Eastern", "703": "US/Eastern", "704": "US/Eastern",
    "706": "US/Eastern", "716": "US/Eastern", "718": "US/Eastern", "732": "US/Eastern",
    "740": "US/Eastern", "754": "US/Eastern", "757": "US/Eastern", "770": "US/Eastern",
    "772": "US/Eastern", "774": "US/Eastern", "781": "US/Eastern", "786": "US/Eastern",
    "802": "US/Eastern", "803": "US/Eastern", "804": "US/Eastern", "813": "US/Eastern",
    "814": "US/Eastern", "828": "US/Eastern", "845": "US/Eastern", "848": "US/Eastern",
    "856": "US/Eastern", "857": "US/Eastern", "860": "US/Eastern", "862": "US/Eastern",
    "863": "US/Eastern", "904": "US/Eastern", "908": "US/Eastern", "910": "US/Eastern",
    "914": "US/Eastern", "917": "US/Eastern", "919": "US/Eastern", "941": "US/Eastern",
    "954": "US/Eastern", "973": "US/Eastern", "978": "US/Eastern",
    # Central
    "205": "US/Central", "210": "US/Central", "214": "US/Central", "217": "US/Central",
    "219": "US/Central", "224": "US/Central", "225": "US/Central", "228": "US/Central",
    "254": "US/Central", "256": "US/Central", "262": "US/Central", "281": "US/Central",
    "309": "US/Central", "312": "US/Central", "314": "US/Central", "316": "US/Central",
    "317": "US/Central", "318": "US/Central", "319": "US/Central", "320": "US/Central",
    "331": "US/Central", "334": "US/Central", "346": "US/Central", "361": "US/Central",
    "385": "US/Central", "402": "US/Central", "405": "US/Central", "409": "US/Central",
    "414": "US/Central", "417": "US/Central", "430": "US/Central", "432": "US/Central",
    "456": "US/Central", "469": "US/Central", "479": "US/Central", "501": "US/Central",
    "502": "US/Central", "504": "US/Central", "507": "US/Central", "512": "US/Central",
    "515": "US/Central", "531": "US/Central", "534": "US/Central", "563": "US/Central",
    "573": "US/Central", "601": "US/Central", "608": "US/Central", "612": "US/Central",
    "615": "US/Central", "618": "US/Central", "620": "US/Central", "630": "US/Central",
    "636": "US/Central", "641": "US/Central", "651": "US/Central", "660": "US/Central",
    "662": "US/Central", "682": "US/Central", "701": "US/Central", "708": "US/Central",
    "713": "US/Central", "715": "US/Central", "717": "US/Central", "720": "US/Central",
    "731": "US/Central", "737": "US/Central", "743": "US/Central", "763": "US/Central",
    "769": "US/Central", "773": "US/Central", "779": "US/Central", "806": "US/Central",
    "815": "US/Central", "816": "US/Central", "817": "US/Central", "830": "US/Central",
    "832": "US/Central", "847": "US/Central", "850": "US/Central", "870": "US/Central",
    "872": "US/Central", "901": "US/Central", "903": "US/Central", "913": "US/Central",
    "915": "US/Central", "920": "US/Central", "936": "US/Central", "940": "US/Central",
    "952": "US/Central", "956": "US/Central", "972": "US/Central", "979": "US/Central",
    # Mountain
    "303": "US/Mountain", "307": "US/Mountain", "385": "US/Mountain", "406": "US/Mountain",
    "435": "US/Mountain", "480": "US/Mountain", "505": "US/Mountain", "520": "US/Mountain",
    "575": "US/Mountain", "602": "US/Mountain", "623": "US/Mountain", "719": "US/Mountain",
    "720": "US/Mountain", "801": "US/Mountain", "928": "US/Mountain",
    # Pacific
    "206": "US/Pacific", "209": "US/Pacific", "213": "US/Pacific", "253": "US/Pacific",
    "310": "US/Pacific", "323": "US/Pacific", "360": "US/Pacific", "408": "US/Pacific",
    "415": "US/Pacific", "424": "US/Pacific", "425": "US/Pacific", "442": "US/Pacific",
    "503": "US/Pacific", "509": "US/Pacific", "510": "US/Pacific", "530": "US/Pacific",
    "541": "US/Pacific", "559": "US/Pacific", "562": "US/Pacific", "619": "US/Pacific",
    "626": "US/Pacific", "628": "US/Pacific", "650": "US/Pacific", "657": "US/Pacific",
    "661": "US/Pacific", "669": "US/Pacific", "702": "US/Pacific", "707": "US/Pacific",
    "714": "US/Pacific", "725": "US/Pacific", "747": "US/Pacific", "760": "US/Pacific",
    "775": "US/Pacific", "805": "US/Pacific", "818": "US/Pacific", "831": "US/Pacific",
    "858": "US/Pacific", "909": "US/Pacific", "916": "US/Pacific", "925": "US/Pacific",
    "949": "US/Pacific", "951": "US/Pacific", "971": "US/Pacific",
}

TZ_LABELS = {
    "US/Eastern": "ET",
    "US/Central": "CT",
    "US/Mountain": "MT",
    "US/Pacific": "PT",
    "US/Hawaii": "HT",
    "US/Alaska": "AKT",
}


def resolve_timezone(contact_props):
    """Resolve timezone using priority: hs_timezone > state > area code."""
    hs_tz = (contact_props.get("hs_timezone") or "").strip()
    if hs_tz:
        return hs_tz

    state = (contact_props.get("state") or "").strip().upper()
    if state and state in STATE_TO_TZ:
        return STATE_TO_TZ[state]

    for phone_field in ["mobilephone", "phone"]:
        phone = (contact_props.get(phone_field) or "").strip()
        digits = re.sub(r"\D", "", phone)
        if len(digits) >= 10:
            if digits.startswith("1") and len(digits) == 11:
                digits = digits[1:]
            area = digits[:3]
            if area in AREA_CODE_TO_TZ:
                return AREA_CODE_TO_TZ[area]

    return "UNKNOWN"


def tz_label(tz):
    return TZ_LABELS.get(tz, tz)
