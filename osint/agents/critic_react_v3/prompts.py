"""Prompt builders, preset library, and ledger parser for critic_react_v3.

Presets are canned `goal` preambles. The user's free-form `goal` (if any)
is appended after the preset preamble in the system prompt.

PRESET_HINTS is a one-line summary of each preset, used by the critic
prompt — the critic doesn't need the full preamble, just enough to know
what kind of investigation it's evaluating.
"""
from __future__ import annotations


PRESETS: dict[str, str] = {
    "coffee_career": (
        "I'm preparing for a coffee chat with this person, focused on their career. "
        "Find their current role and employer, recent shipped work or projects, "
        "any public talks/posts/papers worth referencing, and shared interests "
        "that might come up. Flag anything sensitive to avoid (recent layoff, "
        "controversy, loss). Skip family, addresses, and history older than ~5y "
        "unless directly relevant."
    ),
    "coffee_personal": (
        "I want to know this person better as a friend or new acquaintance. "
        "Find their hobbies, interests, communities they're part of, and recent "
        "public posts I could react to. Skip employment-financial details, "
        "addresses, and anything that feels invasive."
    ),
    "reconnect": (
        "I want to reconnect with this person after time apart. Find what "
        "they've been doing recently — new role, new city, life events, "
        "projects — so I can open the conversation naturally."
    ),
    "sales_outreach": (
        "I'm preparing outreach to this person about a business matter. "
        "Find their company, role, recent public communications, mutual "
        "connections, and topics they care about that I can reference warmly."
    ),
    "dossier": (
        "Build a comprehensive dossier. Be thorough across identity, career, "
        "education, online footprint, network, geography, and history. "
        "Surface concrete identifiers and follow up on each."
    ),
    "general": (
        "Investigate this person with whatever lens makes sense from the "
        "subject description and any user-provided goal."
    ),
}


PRESET_HINTS: dict[str, str] = {
    "coffee_career": "career-focused coffee chat: current role, recent work, talking points, things to avoid.",
    "coffee_personal": "personal coffee chat: hobbies, communities, recent posts, no invasive details.",
    "reconnect": "reconnect with old contact: recent moves, life events, conversation openers.",
    "sales_outreach": "warm outreach: company, role, recent public comms, mutual connections.",
    "dossier": "comprehensive dossier: identity, career, education, footprint, network, history.",
    "general": "free-form investigation guided by the user's goal text.",
}
