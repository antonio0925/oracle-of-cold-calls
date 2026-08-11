"""The note keeps every section Octave returns, in whatever shape it returns it.

The BDR reported "it cuts out at objection handling". A real 11-contact run
showed why: Octave emits the same content in several markdown shapes, and the
formatter recognised exactly one of each.

  headings   `### OUTPUT 1: VOICEMAIL SCRIPT` parsed.
             `**OUTPUT 1: VOICEMAIL SCRIPT**` did not, so the whole note
             collapsed to a bare header. 137 bytes from a 5,187 character
             script.
  objections `**Objection:** "..."` parsed.
             `Objection: "..."` did not, so the objections section rendered
             empty and the note stopped after the live call script.

4 of the 11 notes in that run were damaged. The fixtures in tests/fixtures are
the actual Octave output from it, unedited.
"""
import pathlib
import re

import pytest

from services.formatting import (
    _split_octave_sections, _format_objections_html, _format_blocks_html,
    format_note_html,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
PROPS = {"firstname": "Ann", "lastname": "Diaz", "company": "Acme"}

# HubSpot notes are limited to structural tags. Anything else is a defect.
ALLOWED_TAGS = {"p", "strong", "br", "ul", "li"}


def _fixture(name):
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize("fixture", [
    "octave_markdown_headings.md",   # ### OUTPUT 1: ...
    "octave_bold_output_headings.md",  # **OUTPUT 1: ...**
    "octave_plain_objections.md",    # ### headings, plain "Objection:" lines
])
def test_every_shape_keeps_all_three_script_sections(fixture):
    sections = _split_octave_sections(_fixture(fixture))
    for key in ("voicemail", "live_call", "objections"):
        assert sections[key], f"{fixture} lost the {key} section"


def test_bold_output_headings_do_not_collapse_the_note():
    # This fixture rendered as a 137 byte header before the fix.
    html = format_note_html(PROPS, _fixture("octave_bold_output_headings.md"))
    assert len(html) > 4000
    for heading in ("VOICEMAIL SCRIPT", "LIVE CALL SCRIPT", "OBJECTION HANDLING"):
        assert heading in html


def test_plain_objection_lines_still_produce_objections():
    # This is the reported symptom: the note ended at objection handling.
    objections = _split_octave_sections(_fixture("octave_plain_objections.md"))["objections"]
    html = _format_objections_html(objections)
    assert html.count("<li>") >= 6, "plain-text responses were dropped"
    assert "“" in html, "the objection itself was dropped"


def test_a_sub_heading_is_not_treated_as_a_section_break():
    # `#### Opener` inside the live call script is content, not a new section.
    script = (
        "### OUTPUT 2: LIVE CALL SCRIPT\n"
        "#### Opener\n- say hello\n"
        "### OUTPUT 3: POTENTIAL OBJECTIONS\n"
        'Objection: "no time"\n- fair, twenty minutes\n'
    )
    sections = _split_octave_sections(script)
    assert "Opener" in sections["live_call"]
    assert sections["objections"]


# --- the bullets the BDR reads aloud ---------------------------------------

def test_bullets_render_as_a_list_not_as_a_paragraph():
    # She read written-out sentences word for word and sounded like she was
    # reading. Bullets must survive as bullets.
    html = _format_blocks_html("- first beat\n- second beat\n- third beat")
    assert html.count("<li>") == 3
    assert "<p>" not in html


def test_a_label_line_becomes_a_heading_above_its_bullets():
    html = _format_blocks_html("**OPENER**\n- say hello\n\nTHE ASK:\n- twenty minutes")
    assert "<strong>OPENER:</strong>" in html
    assert "<strong>THE ASK:</strong>" in html
    assert html.count("<ul>") == 2


def test_prose_still_renders_as_prose():
    # The intel block she reads before dialling is sentences, not bullets.
    html = _format_blocks_html("Airbyte is a data platform.\nIt sells two ways.")
    assert "<p>" in html and "<li>" not in html


def test_numbered_and_dashed_bullets_both_count():
    assert _format_blocks_html("1. one\n2. two").count("<li>") == 2
    assert _format_blocks_html("* one\n- two\n• three").count("<li>") == 3


# --- the intel section ------------------------------------------------------

_WITH_INTEL = """### OUTPUT 0: CALL INTEL

**Why they qualified**
They run two motions at once.

**Opening question**
"How do you keep positioning aligned?"

### OUTPUT 1: VOICEMAIL SCRIPT

- Theresa from Octave
- Twenty minutes this week
"""


def test_the_intel_section_is_parsed():
    sections = _split_octave_sections(_WITH_INTEL)
    assert "two motions" in sections["intel"]
    assert "Theresa from Octave" in sections["voicemail"]


def test_the_intel_leads_the_note():
    # She asked to read the context before the words.
    html = format_note_html(PROPS, _WITH_INTEL)
    assert "READ THIS FIRST" in html
    assert html.index("READ THIS FIRST") < html.index("VOICEMAIL SCRIPT")


def test_a_note_without_intel_is_unchanged():
    # Scripts generated before the intel section existed must still render.
    html = format_note_html(PROPS, _fixture("octave_markdown_headings.md"))
    assert "READ THIS FIRST" not in html
    assert "VOICEMAIL SCRIPT" in html


# --- safety -----------------------------------------------------------------

@pytest.mark.parametrize("fixture", [
    "octave_markdown_headings.md",
    "octave_bold_output_headings.md",
    "octave_plain_objections.md",
])
def test_notes_carry_only_structural_tags(fixture):
    html = format_note_html(PROPS, _fixture(fixture))
    used = set(re.findall(r'</?([a-z0-9]+)', html))
    assert used <= ALLOWED_TAGS, f"unexpected tags: {used - ALLOWED_TAGS}"


def test_a_hostile_bullet_cannot_inject_markup():
    html = _format_blocks_html('- <img src=x onerror="alert(1)">')
    assert "<img" not in html
    assert "&lt;img" in html
