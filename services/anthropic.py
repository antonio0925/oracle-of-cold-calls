"""
Anthropic Claude API client for follow-up email generation.

Uses the proven prompts from vm-followup-webhook/regen-all.js (60/60 success rate).
Direct Claude API — NOT routed through Octave.
"""
import requests as http_requests
from services.retry import retry_request


def generate_followup_email(api_key, disposition, first_name, company_name, original_subject):
    """Generate a VM or GFY follow-up email via Claude.

    Args:
        api_key: Anthropic API key
        disposition: "voicemail" or "gfy"
        first_name: Contact's first name
        company_name: Contact's company
        original_subject: Subject line of the original cold email (for threading context)

    Returns:
        str: Generated email body with em-dashes stripped
    """
    if disposition == "gfy":
        prompt = _gfy_prompt(first_name, company_name, original_subject)
    else:
        prompt = _vm_prompt(first_name, company_name, original_subject)

    r = retry_request(
        lambda: http_requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "content-type": "application/json",
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": "claude-sonnet-4-5-20250929",
                "max_tokens": 300,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=(10, 30),
        ),
        label="Anthropic Messages API",
    )
    r.raise_for_status()
    data = r.json()

    text = data["content"][0]["text"].strip()
    # Hard strip em-dashes -- LLMs love them, emails shouldn't have them
    text = text.replace("\u2014", " - ")
    return text


def _vm_prompt(first_name, company_name, original_subject):
    """Voicemail follow-up prompt. Proven 60/60 from regen-all.js."""
    return (
        f"You are Antonio. You're a real person, Head of Sales at a startup. "
        f"You just cold called {first_name} at {company_name} and left a voicemail. "
        f"Now you're firing off a quick follow-up email that will thread as a reply "
        f'under your original cold email (subject: "{original_subject}").\n\n'
        "THE SITUATION:\n"
        "- Touch 1 was the cold email (they already have it, don't repeat ANY of it)\n"
        "- Touch 2 was the voicemail you just left\n"
        "- Touch 3 is THIS email - a quick note after hanging up the phone\n\n"
        "WHAT THIS EMAIL SHOULD FEEL LIKE:\n"
        "A human who just hung up the phone and is shooting a quick 3-line note. "
        "Not a content machine that read a brief. You're not summarizing anything. "
        'You\'re not referencing "what I mentioned" or "the positioning gap" or any '
        "specific angle. They got the email. They got the VM. Now you're just being "
        "direct: let's grab 20 minutes.\n\n"
        "WRITE THIS:\n"
        "- 2-4 sentences. That's it.\n"
        f'- Open with something natural like "{first_name}," or "Hey {first_name}," '
        "- then mention you just left a VM\n"
        "- The ask: coffee, lunch, or 20 minutes. You'll do all the prep, they just "
        "show up. If it's not a fit they can tell you to kick rocks.\n"
        '- Sign off as "Antonio" - no title, no company, no phone number\n\n'
        "DO NOT:\n"
        "- Reference the content of the original email (no \"the angle I mentioned\", "
        "no \"that positioning gap\", no \"narrative tension\", no product names, no statistics)\n"
        "- Re-pitch anything\n"
        "- Use em dashes\n"
        "- Include a subject line\n"
        "- Include any preamble, labels, or commentary - output ONLY the email body\n\n"
        "Go."
    )


def _gfy_prompt(first_name, company_name, original_subject):
    """GFY (hung up) follow-up prompt. Proven from regen-all.js."""
    return (
        f"You are Antonio. You're Head of Sales at a startup. "
        f"You just cold called {first_name} at {company_name} and they hung up on you "
        f'the second they heard "cold call." Now you\'re sending a follow-up email '
        f'that threads under your original cold email (subject: "{original_subject}").\n\n'
        "THE SITUATION:\n"
        "- They got your cold email (touch 1)\n"
        "- You called, they hung up immediately (touch 2)\n"
        "- This email is touch 3\n\n"
        "WHAT THIS EMAIL SHOULD FEEL LIKE:\n"
        "Cheeky, self-aware, but not bitter. You're the kind of person who laughs when "
        "someone hangs up and respects the move. You're not going to re-pitch them - they "
        "already have the email for that. Instead, acknowledge the hang-up with humor, "
        "then make it personal to THEIR world (their company, their role, what they're "
        "probably dealing with day-to-day). Close with a meeting ask.\n\n"
        "WRITE THIS:\n"
        "- 3-5 sentences\n"
        "- Open by acknowledging the hang-up with humor (e.g. \"Fair enough on the hang-up\" "
        "or \"Respect the quick trigger\" - make it YOUR voice, not a template)\n"
        f"- 1-2 sentences about why 20 minutes with you would be worth it for THEM "
        f"specifically - not your product pitch, but what's in it for them given what "
        f"{company_name} is dealing with right now\n"
        "- Close with the ask: coffee, lunch, 20 minutes. Low commitment.\n"
        '- Sign off as "Antonio" - no title, no company\n\n'
        "DO NOT:\n"
        "- Repeat the pitch from the original email (no quoting stats, no restating "
        "the angle, no product positioning)\n"
        "- Use em dashes\n"
        "- Include a subject line\n"
        "- Include any preamble, labels, or commentary - output ONLY the email body\n\n"
        "Go."
    )
