"""
HTML note formatting — transforms Octave markdown into structured HubSpot notes.

Also includes normalize_html_for_compare used by the cleanup routes.
"""
import html as _html
import re
from datetime import date, datetime


def _esc(text):
    """Escape text for insertion into the note HTML.

    Everything this module emits is written to a HubSpot contact record that
    other people read. Two of the three inputs are outside our control: the
    contact's own CRM fields, and Octave's generated output. Relying on
    HubSpot to sanitise what we send is a trust boundary we do not need.

    Only the structural tags this module builds are literal HTML. Every piece
    of text passes through here first.
    """
    return _html.escape(str(text or ""), quote=True)


def _esc_block(text):
    """Escape a block of text, then restore its line breaks as <br>."""
    return _esc(text).replace(chr(10), "<br>")


def clean_clock(raw):
    """Normalise a browser time input to "HH:MM", or "" if it is not one.

    An <input type="time"> submits 24-hour "HH:MM", and "HH:MM:SS" in some
    browsers. Anything else is dropped rather than stored, because this value
    is written onto HubSpot contact records and read back by the BDR.
    """
    if not raw:
        return ""
    match = re.match(r'^\s*([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?\s*$', str(raw))
    if not match:
        return ""
    return f"{int(match.group(1)):02d}:{match.group(2)}"


def format_clock(hhmm):
    """Render "09:00" as "9:00 AM". Returns "" for anything unparseable."""
    cleaned = clean_clock(hhmm)
    if not cleaned:
        return ""
    hour, minute = (int(p) for p in cleaned.split(":"))
    period = "AM" if hour < 12 else "PM"
    display = hour % 12 or 12
    return f"{display}:{minute:02d} {period}"


def format_plan_day(calling_date):
    """Render "2026-08-12" as "Wednesday Aug 12". Falls back to the input."""
    if not calling_date:
        return ""
    try:
        parsed = datetime.strptime(str(calling_date).strip()[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return str(calling_date)
    return parsed.strftime("%A %b %d")


def format_plan_stamp(calling_date, calling_time, tz_abbrev=""):
    """One line naming the day and the start time of a call plan.

    Empty parts are left out, so a plan with no time still reads correctly.
    """
    day = format_plan_day(calling_date)
    clock = format_clock(calling_time)
    if clock and tz_abbrev:
        clock = f"{clock} {tz_abbrev}"
    if day and clock:
        return f"{day}, from {clock}"
    return day or clock


# A section heading, in any shape Octave has been observed to emit:
#   ### OUTPUT 1: VOICEMAIL SCRIPT      markdown heading
#   ## Voicemail Script                 markdown heading, no OUTPUT prefix
#   **OUTPUT 1: VOICEMAIL SCRIPT**      bold, no hashes
#   OUTPUT 1: VOICEMAIL SCRIPT          bare line
#
# The shape changes between runs of the same agent on the same list. Matching
# only one of them is what silently dropped whole sections: 4 of 11 notes in a
# real 11-contact run were damaged, one of them down to a bare header.
#
# `(?!#)` on the markdown form keeps `#### Opener` sub-headings as content.
_SECTION_HEADING = re.compile(
    r'(?m)^[ \t]*(?:'
    r'#{2,3}(?!#)[ \t]*'            # ## or ###
    r'|\*\*[ \t]*(?=[^\n]*\*\*)'    # **...**
    r')?'
    r'(?:OUTPUT[ \t]*\d+[ \t]*[:.\-][ \t]*)?'
    r'(?P<name>VOICEMAIL[ \t]*SCRIPT'
    r'|LIVE[ \t]*CALL[ \t]*SCRIPT'
    r'|CALL[ \t]*SCRIPT'
    r'|(?:POTENTIAL[ \t]*)?OBJECTIONS?(?:[ \t]*HANDLING)?'
    r'|CALL[ \t]*INTEL'
    r'|INTEL)'
    r'[ \t]*:?[ \t]*\*{0,2}[ \t]*$',
    re.IGNORECASE,
)

_SECTION_KEYS = (
    ("VOICEMAIL", "voicemail"),
    ("LIVE CALL", "live_call"),
    ("CALL SCRIPT", "live_call"),
    ("OBJECTION", "objections"),
    ("INTEL", "intel"),
)


def _section_key(heading_name):
    """Map a matched heading to a section key."""
    flat = re.sub(r'[ \t]+', ' ', heading_name).strip().upper()
    for needle, key in _SECTION_KEYS:
        if needle in flat:
            return key
    return None


def _split_octave_sections(script_content):
    """Split Octave output into its named sections.

    Returns a dict with intel, voicemail, live_call, and objections. A section
    Octave did not emit comes back as "".
    """
    sections = {"intel": "", "voicemail": "", "objections": "", "live_call": ""}
    if not script_content:
        return sections

    matches = list(_SECTION_HEADING.finditer(script_content))
    for i, match in enumerate(matches):
        key = _section_key(match.group("name"))
        if not key:
            continue
        start = match.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(script_content)
        body = script_content[start:end].strip()
        # Later headings win only when the earlier one captured nothing, so a
        # stray mention cannot blank a real section.
        if body and not sections[key]:
            sections[key] = body

    return sections


def _strip_md(text):
    """Strip markdown formatting to plain text: remove **bold**, *italic*, etc."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    return text


# A bullet, in any shape: "- x", "* x", "• x", "1. x".
_BULLET = re.compile(r'^[ \t]*(?:[\*\-•–]|\d+[.)])[ \t]+(?P<text>.+)$')

# A label on its own line: "**OPENER**", "**Opener:**", "OPENER:", "THE ASK".
_BOLD_LABEL = re.compile(r'^[ \t]*\*\*[ \t]*(?P<text>[^*]+?)[ \t]*:?[ \t]*\*\*[ \t]*:?[ \t]*$')
_CAPS_LABEL = re.compile(r'^[ \t]*(?P<text>[A-Z][A-Z0-9 \'/&()-]{2,}?)[ \t]*:[ \t]*$')

# A horizontal rule.
_RULE = re.compile(r'^[ \t]*[\*\-_]{3,}[ \t]*$')


def _format_blocks_html(text):
    """Render a section into HubSpot-safe HTML.

    Handles the three shapes that appear in one section, mixed:
      a label on its own line, a bullet list, and a run of prose.

    The BDR reads the scripts out loud. Bullets stay bullets, because a
    paragraph is what made her read word for word and sound like she was
    reading. Prose is kept only where it is genuinely prose, such as the
    intel she reads before dialling.

    Only p, strong, br, ul, and li are emitted. Everything else is escaped.
    """
    if not text:
        return ""

    parts = []
    bullets = []
    prose = []

    def flush_bullets():
        if bullets:
            parts.append("<ul>" + "".join(f"<li>{_esc(b)}</li>" for b in bullets) + "</ul>")
            bullets.clear()

    def flush_prose():
        if prose:
            joined = " ".join(prose).strip()
            if joined:
                parts.append(f"<p>{_esc(joined)}</p>")
            prose.clear()

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        if not line.strip() or _RULE.match(line):
            flush_bullets()
            flush_prose()
            continue

        label = _BOLD_LABEL.match(line) or _CAPS_LABEL.match(line)
        if label:
            flush_bullets()
            flush_prose()
            parts.append(f"<p><strong>{_esc(_strip_md(label.group('text')).strip())}:</strong></p>")
            continue

        bullet = _BULLET.match(line)
        if bullet:
            flush_prose()
            bullets.append(_strip_md(bullet.group("text")).strip())
            continue

        flush_bullets()
        prose.append(_strip_md(line).strip())

    flush_bullets()
    flush_prose()
    return "".join(parts)


# An objection line, bold or plain: '**Objection:** "x"', 'Objection: "x"',
# 'TIMING: "x"'. Octave emits all three, and which one it picks changes between
# runs. Only the bold form used to parse, so a plain-text run rendered an empty
# objections section and the note stopped after the live call script. That is
# the "it cuts out at objection handling" report.
_OBJECTION = re.compile(
    r'^[ \t]*(?:\*\*)?[ \t]*'
    r'(?:(?P<category>[A-Z][A-Z \'/&-]{2,})|OBJECTION)'
    r'[ \t]*\d*[ \t]*:?[ \t]*(?:\*\*)?[ \t]*:?[ \t]*'
    r'(?P<text>.+?)[ \t]*$',
    re.IGNORECASE,
)

# A response line, bold or plain: '**Response 1:** "x"', 'Response 1: "x"'.
_RESPONSE = re.compile(
    r'^[ \t]*(?:[\*\-•][ \t]*)?(?:\*\*)?[ \t]*'
    r'RESPONSES?[ \t]*\d*[ \t]*:?[ \t]*(?:\*\*)?[ \t]*:?[ \t]*'
    r'(?P<text>.*)$',
    re.IGNORECASE,
)


def _clean_quoted(text):
    """Strip wrapping quotes and stray markdown from an objection or response."""
    out = _strip_md(text).strip()
    out = out.strip('“”"‘’').strip()
    return out


def _format_objections_html(obj_text):
    """Format objections: a quoted objection, then its responses as bullets."""
    if not obj_text:
        return ""

    blocks = []
    category = objection = None
    responses = []

    def flush():
        if objection:
            blocks.append((category, objection, list(responses)))

    for raw_line in obj_text.split("\n"):
        line = raw_line.strip()
        if not line or _RULE.match(line):
            continue

        response = _RESPONSE.match(line)
        if response:
            text = _clean_quoted(response.group("text"))
            if text:
                responses.append(text)
            continue

        objection_match = _OBJECTION.match(line)
        if objection_match and objection_match.group("text").strip():
            flush()
            raw_category = objection_match.group("category")
            # "Objection" is the field name, not a category worth showing.
            category = None
            if raw_category and raw_category.strip().upper() != "OBJECTION":
                category = raw_category.strip()
            objection = _clean_quoted(objection_match.group("text"))
            responses = []
            continue

        bullet = _BULLET.match(line)
        if bullet and objection:
            text = _clean_quoted(bullet.group("text"))
            if text:
                responses.append(text)
            continue

    flush()

    html_parts = []
    for category, objection, responses in blocks:
        if category:
            html_parts.append(
                f"<p><strong>{_esc(category)}:</strong> “{_esc(objection)}”</p>"
            )
        else:
            html_parts.append(f"<p><strong>“{_esc(objection)}”</strong></p>")
        if responses:
            html_parts.append("<ul>")
            for r in responses:
                html_parts.append(f"<li>{_esc(r)}</li>")
            html_parts.append("</ul>")

    return "".join(html_parts)


def format_note_html(contact_props, script_content,
                     calling_date="", calling_time="", tz_abbrev=""):
    """Transform Octave markdown output into a structured HubSpot note.

    Format:
      🔥 COLD CALL PREP - First Last | Company
      Generated YYYY-MM-DD
      Call day: Wednesday Aug 12, from 9:00 AM PT
      🧭 READ THIS FIRST ... (why they qualified, segment fit, signals, angle)
      📞 VOICEMAIL SCRIPT  ...
      🎯 LIVE CALL SCRIPT  ... (with OPENER/HOOK/ASK/ENGAGE/SHUT IT DOWN)
      🛡️ OBJECTION HANDLING ... (with category + quote + bullet responses)

    The intel comes first because the BDR asked to read the context before the
    words. Everything below it is bullets, not sentences, because reading a
    written-out script aloud is what made her sound like she was reading.

    The call day line is the plan the BDR set, not the day the script was
    written. The two differ whenever a route is planned the day before it is
    worked, which is the normal case.
    """
    first = contact_props.get("firstname", "")
    last = contact_props.get("lastname", "")
    company = contact_props.get("company", "")
    today_str = date.today().strftime("%Y-%m-%d")
    plan_stamp = format_plan_stamp(calling_date, calling_time, tz_abbrev)

    sections = _split_octave_sections(script_content)

    parts = []

    # ── Header ──
    header = (
        f"<p><strong>\U0001f525 COLD CALL PREP - "
        f"{_esc(first)} {_esc(last)} | {_esc(company)}</strong></p>"
        f"<p>Generated {today_str}</p>"
    )
    if plan_stamp:
        header += f"<p>Call day: {_esc(plan_stamp)}</p>"
    parts.append(header)

    # ── Read this first ──
    # The intel she reads before dialling. It leads, because she asked to
    # start from the context rather than from the words.
    if sections["intel"]:
        parts.append(
            f"<p><strong>\U0001f9ed READ THIS FIRST</strong></p>"
            f"{_format_blocks_html(sections['intel'])}"
        )

    # ── Voicemail ──
    if sections["voicemail"]:
        parts.append(
            f"<p><strong>\U0001f4de VOICEMAIL SCRIPT</strong></p>"
            f"{_format_blocks_html(sections['voicemail'])}"
        )

    # ── Live Call Script ──
    if sections["live_call"]:
        parts.append(
            f"<p><strong>\U0001f3af LIVE CALL SCRIPT</strong></p>"
            f"{_format_blocks_html(sections['live_call'])}"
        )

    # ── Objection Handling ──
    if sections["objections"]:
        parts.append(
            f"<p><strong>\U0001f6e1\ufe0f OBJECTION HANDLING</strong></p>"
            f"{_format_objections_html(sections['objections'])}"
        )

    return "<br>".join(parts)


def normalize_html_for_compare(html):
    """Normalize HTML to a stable string for comparison.
    HubSpot may alter whitespace, entity encoding, etc.
    We strip it all down to just visible text content.
    """
    if not html:
        return ""
    # Remove all HTML tags
    text = re.sub(r'<[^>]+>', ' ', html)
    # Decode entities before comparing. Notes generated now are escaped and
    # notes already in HubSpot are not, so "Smith &amp; Co" and "Smith & Co"
    # must compare equal. Without this, the cleanup would read every existing
    # note containing &, <, or a quote as different from its replacement.
    text = _html.unescape(text)
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Normalize unicode quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    return text
