"""
HTML note formatting — transforms Octave markdown into structured HubSpot notes.

Also includes normalize_html_for_compare used by the cleanup routes.
"""
import re
from datetime import date


def _split_octave_sections(script_content):
    """Split Octave output into voicemail, objections, and live call sections."""
    sections = {"voicemail": "", "objections": "", "live_call": ""}

    # Octave uses: ### OUTPUT 1: VOICEMAIL SCRIPT, ### OUTPUT 2: ..., ### OUTPUT 3: ...
    # Also handle without "OUTPUT N:" prefix: ### VOICEMAIL SCRIPT
    parts = re.split(r'###\s*(?:OUTPUT\s*\d+\s*:\s*)?', script_content, flags=re.IGNORECASE)

    for part in parts:
        stripped = part.strip()
        upper = stripped[:60].upper()
        if upper.startswith("VOICEMAIL"):
            sections["voicemail"] = re.sub(
                r'^VOICEMAIL\s*SCRIPT\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()
        elif upper.startswith("POTENTIAL OBJECTION") or upper.startswith("OBJECTION"):
            sections["objections"] = re.sub(
                r'^(?:POTENTIAL\s*)?OBJECTIONS?\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()
        elif upper.startswith("LIVE CALL") or upper.startswith("CALL SCRIPT"):
            sections["live_call"] = re.sub(
                r'^(?:LIVE\s*)?CALL\s*SCRIPT\s*\n*', '', stripped, flags=re.IGNORECASE
            ).strip()

    return sections


def _strip_md(text):
    """Strip markdown formatting to plain text: remove **bold**, *italic*, etc."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)', r'\1', text)
    return text


def _format_voicemail_html(vm_text):
    """Format voicemail as clean HTML paragraphs."""
    if not vm_text:
        return ""
    clean = _strip_md(vm_text.strip())
    # Remove markdown horizontal rules
    clean = re.sub(r'^[\*\-_]{3,}\s*$', '', clean, flags=re.MULTILINE)
    # Convert double newlines to paragraph breaks, single newlines to <br>
    paragraphs = re.split(r'\n\s*\n', clean)
    return "".join(f"<p>{p.strip().replace(chr(10), '<br>')}</p>" for p in paragraphs if p.strip())


def _format_live_call_html(lc_text):
    """Format live call script: OPENER/HOOK/ASK/ENGAGE/SHUT IT DOWN subsections."""
    if not lc_text:
        return ""

    blocks = []
    current_label = None
    current_lines = []

    for line in lc_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            if current_lines:
                current_lines.append("")
            continue

        # Detect section headers: **OPENER:** or **THE HOOK:** (with colon inside or outside bold)
        header_match = re.match(r'^\*\*([A-Z][A-Z\s\':]+?):?\*\*:?\s*$', stripped)
        # Also match plain "OPENER:" style
        if not header_match:
            header_match = re.match(r'^([A-Z][A-Z\s\':]{3,}):?\s*$', stripped)

        if header_match:
            if current_label is not None or current_lines:
                blocks.append((current_label, "\n".join(current_lines).strip()))
            current_label = header_match.group(1).strip().rstrip(":")
            current_lines = []
        else:
            current_lines.append(stripped)

    if current_label is not None or current_lines:
        blocks.append((current_label, "\n".join(current_lines).strip()))

    html_parts = []
    for label, content in blocks:
        if not content and not label:
            continue
        # Strip markdown from content, preserve structure
        content = _strip_md(content)
        # Convert paragraphs (double newline) and lines
        paragraphs = re.split(r'\n\s*\n', content)
        content_html = "".join(
            f"<p>{p.strip().replace(chr(10), '<br>')}</p>"
            for p in paragraphs if p.strip()
        )
        if label:
            html_parts.append(f"<p><strong>{label}:</strong></p>{content_html}")
        else:
            html_parts.append(content_html)

    return "".join(html_parts)


def _format_objections_html(obj_text):
    """Format objections: each with a quote header and bullet-point responses."""
    if not obj_text:
        return ""

    blocks = []
    current_category = None
    current_objection = None
    current_responses = []

    for line in obj_text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue

        # Match: **Objection:** "text"
        obj_match = re.match(
            r'^\*\*(?:Objection|OBJECTION)\s*:?\*\*\s*["\u201c](.+?)["\u201d]?\s*$',
            stripped,
        )
        # Match category-style: TIMING: "text" or STATUS QUO: "text"
        cat_match = re.match(
            r'^([A-Z][A-Z\s/\-]+?):\s*["\u201c](.+?)["\u201d]?\s*$',
            stripped,
        )

        if obj_match:
            if current_objection:
                blocks.append((current_category, current_objection, current_responses))
            current_category = None
            current_objection = obj_match.group(1).strip().rstrip('"').rstrip('\u201d')
            current_responses = []
        elif cat_match and not stripped.startswith("*"):
            if current_objection:
                blocks.append((current_category, current_objection, current_responses))
            current_category = cat_match.group(1).strip()
            current_objection = cat_match.group(2).strip().rstrip('"').rstrip('\u201d')
            current_responses = []
        elif stripped.startswith("**Response") or stripped.startswith("**Responses"):
            # Could be just a header "**Responses:**" OR inline "**Response 1:** actual text"
            inline = re.sub(r'^\*\*Responses?\s*\d*\s*:?\*\*:?\s*', '', stripped).strip()
            if inline:
                current_responses.append(_strip_md(inline))
            # else: bare header line like "**Responses:**" — skip it
        elif re.match(r'^[\*\-\u2022]\s+', stripped):
            resp = re.sub(r'^[\*\-\u2022]\s+', '', stripped).strip()
            resp = re.sub(r'^\*\*Response\s*\d*:?\*\*\s*', '', resp)
            current_responses.append(_strip_md(resp))

    if current_objection:
        blocks.append((current_category, current_objection, current_responses))

    html_parts = []
    for category, objection, responses in blocks:
        if category:
            html_parts.append(f"<p><strong>{category}:</strong> \u201c{objection}\u201d</p>")
        else:
            html_parts.append(f"<p><strong>\u201c{objection}\u201d</strong></p>")
        if responses:
            html_parts.append("<ul>")
            for r in responses:
                html_parts.append(f"<li>{r}</li>")
            html_parts.append("</ul>")

    return "".join(html_parts)


def format_note_html(contact_props, campaign, script_content):
    """Transform Octave markdown output into a structured HubSpot note.

    Format:
      🔥 COLD CALL PREP - First Last | Company
      Campaign | Generated YYYY-MM-DD
      📞 VOICEMAIL SCRIPT  ...
      🎯 LIVE CALL SCRIPT  ... (with OPENER/HOOK/ASK/ENGAGE/SHUT IT DOWN)
      🛡️ OBJECTION HANDLING ... (with category + quote + bullet responses)
    """
    first = contact_props.get("firstname", "")
    last = contact_props.get("lastname", "")
    company = contact_props.get("company", "")
    today_str = date.today().strftime("%Y-%m-%d")

    sections = _split_octave_sections(script_content)

    parts = []

    # ── Header ──
    parts.append(
        f"<p><strong>\U0001f525 COLD CALL PREP - {first} {last} | {company}</strong></p>"
        f"<p>{campaign} | Generated {today_str}</p>"
    )

    # ── Voicemail ──
    if sections["voicemail"]:
        parts.append(
            f"<p><strong>\U0001f4de VOICEMAIL SCRIPT</strong></p>"
            f"{_format_voicemail_html(sections['voicemail'])}"
        )

    # ── Live Call Script ──
    if sections["live_call"]:
        parts.append(
            f"<p><strong>\U0001f3af LIVE CALL SCRIPT</strong></p>"
            f"{_format_live_call_html(sections['live_call'])}"
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
    # Collapse whitespace
    text = re.sub(r'\s+', ' ', text).strip()
    # Normalize unicode quotes
    text = text.replace('\u201c', '"').replace('\u201d', '"')
    text = text.replace('\u2014', '-').replace('\u2013', '-')
    return text
