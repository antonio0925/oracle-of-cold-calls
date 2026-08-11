"""Note HTML written to HubSpot contact records.

Two of the three inputs are outside our control: the contact's own CRM fields
and Octave's generated output. Relying on HubSpot to sanitise what we send is
a trust boundary we do not need.
"""
import re

from services.formatting import format_note_html, normalize_html_for_compare

STRUCTURAL = {"p", "strong", "br", "ul", "li"}


def _tags(html):
    return set(re.findall(r"</?([a-zA-Z][a-zA-Z0-9]*)", html))


def test_only_structural_tags_survive_a_hostile_contact_record():
    html = format_note_html(
        {
            "firstname": '"><script>alert(1)</script>',
            "lastname": "<img src=x onerror=alert(1)>",
            "company": "<iframe src=//evil</iframe>",
        },
        "### VOICEMAIL SCRIPT\nHi there",
    )
    assert _tags(html) <= STRUCTURAL, _tags(html) - STRUCTURAL


def test_only_structural_tags_survive_hostile_octave_output():
    # The model's output is not trusted markup either.
    html = format_note_html(
        {"firstname": "Ann", "lastname": "Lee", "company": "Acme"},
        "### VOICEMAIL SCRIPT\n<script>fetch('//evil')</script>\n\n"
        "### LIVE CALL SCRIPT\n**OPENER:**\n<img src=x onerror=alert(1)>\n\n"
        "### POTENTIAL OBJECTIONS\nTIMING: \"<b>busy</b>\"\n* <a href=//evil>click</a>\n",
    )
    assert _tags(html) <= STRUCTURAL, _tags(html) - STRUCTURAL


def test_dangerous_text_is_kept_but_neutralised():
    # Escaping must not silently delete content the BDR needs to read.
    html = format_note_html(
        {"firstname": "Ann", "lastname": "Lee", "company": "Acme"},
        "### VOICEMAIL SCRIPT\nAsk about their <legacy> system",
    )
    assert "&lt;legacy&gt;" in html
    assert "<legacy>" not in html


def test_ordinary_punctuation_still_reads_correctly():
    html = format_note_html(
        {"firstname": "Ann", "lastname": "O'Neil", "company": "Smith & Co"},
        "### VOICEMAIL SCRIPT\nQuick one about Smith & Co.",
    )
    # Escaped in the source...
    assert "&amp;" in html
    # ...and correct once rendered.
    assert "Smith & Co" in normalize_html_for_compare(html)
    assert "O'Neil" in normalize_html_for_compare(html)


def test_an_existing_unescaped_note_still_matches_its_escaped_replacement():
    """The cleanup decides what to archive by comparing normalised notes.

    Notes already in HubSpot were written before escaping and hold a literal
    ampersand. Without entity decoding in the normaliser, every one of them
    would read as different from its replacement and the cleanup would churn.
    """
    old = ("<p><strong>COLD CALL PREP - Ann Lee | Smith & Co</strong></p>"
           "<p>Generated 2026-08-10</p>")
    new = ("<p><strong>COLD CALL PREP - Ann Lee | Smith &amp; Co</strong></p>"
           "<p>Generated 2026-08-10</p>")
    assert normalize_html_for_compare(old) == normalize_html_for_compare(new)


def test_the_note_still_has_its_structure():
    html = format_note_html(
        {"firstname": "Ann", "lastname": "Lee", "company": "Acme"},
        "### VOICEMAIL SCRIPT\nHi Ann\n\n"
        "### LIVE CALL SCRIPT\n**OPENER:**\nGot a second?\n\n"
        "### POTENTIAL OBJECTIONS\nTIMING: \"Busy\"\n* Push back\n",
    )
    for heading in ["COLD CALL PREP", "VOICEMAIL SCRIPT", "LIVE CALL SCRIPT",
                    "OBJECTION HANDLING"]:
        assert heading in html
    assert "<li>" in html and "OPENER" in html


# --- Octave section parsing -------------------------------------------------
# The agent emits "#### Opener" style sub-headings inside the live call script.
# The original splitter matched a bare "###" anywhere, so it also matched the
# first three hashes of "####" and shredded the section: fragments began
# "# Opener" instead of "LIVE CALL", matched nothing, and the whole live call
# script vanished from the note without any error.

from services.formatting import _split_octave_sections

NEW_FORMAT = """### Voicemail Script

"Dylan, it's Theresa from Octave."

---

### Live Call Script

#### Opener
"Hey, this is Theresa from Octave."

#### Hook and Bridge
"At a high level, we're a platform purpose built to..."

#### The Ask
"We built something specifically for you."

---

### Potential Objections

**Objection:** "We're mid launch."
"""

OLD_FORMAT = """### OUTPUT 1: VOICEMAIL SCRIPT
Hi there, it's Theresa.

### OUTPUT 2: POTENTIAL OBJECTIONS
Objection: "No time"

### OUTPUT 3: LIVE CALL SCRIPT
**OPENER:**
Hey there.
"""


def test_sub_headings_do_not_destroy_the_live_call_script():
    s = _split_octave_sections(NEW_FORMAT)
    assert s["live_call"], "the live call script was dropped entirely"
    for beat in ["Opener", "Hook and Bridge", "The Ask"]:
        assert beat in s["live_call"], f"lost the {beat} beat"


def test_all_three_sections_survive_the_new_format():
    s = _split_octave_sections(NEW_FORMAT)
    assert "Theresa" in s["voicemail"]
    assert "mid launch" in s["objections"]


def test_the_older_output_n_format_still_parses():
    s = _split_octave_sections(OLD_FORMAT)
    assert "Theresa" in s["voicemail"]
    assert "No time" in s["objections"]
    assert "OPENER" in s["live_call"]


def test_the_live_call_script_reaches_the_note():
    # The end-to-end consequence: a parsed-away section means a note with a
    # heading and nothing under it.
    html = format_note_html(
        {"firstname": "Dylan", "lastname": "Lee", "company": "Cleo"}, NEW_FORMAT
    )
    assert "LIVE CALL SCRIPT" in html
    assert "Hook and Bridge" in html
    assert "We built something specifically for you" in html
