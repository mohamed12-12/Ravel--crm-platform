"""Single source of truth for the dialect/keyword vocabulary used across
tool_calling_runtime.py's intent-detection helpers and validation_rules.py's
normalizers. Before this module existed, the same kind of phrase list
(trip-type words, affirmative replies, human-handoff requests, trip-quality
questions, trip-discovery phrasing, explanation requests, restart signals)
was independently duplicated across half a dozen call sites -- each one
Egyptian/MSA-flavored, with no Gulf or Franco-Arabic coverage anywhere, and
no shared place to extend when a new dialect variant is seen in a live
transcript. Each set below is keyed by semantic concept, not by call site,
so a new dialect term is added once and every consumer picks it up.

Phase 1 (this module, as first landed) is a byte-for-byte EXTRACTION only --
every entry below is copied verbatim from its prior single call site, with
zero new vocabulary. New Gulf/Franco-Arabic terms are added in a separate,
later change once this extraction is confirmed behavior-preserving against
the full test suite. This module intentionally holds only *data*, never
matching logic -- the matching functions (substring/alias lookup) stay in
their existing homes (tool_calling_runtime.py, validation_rules.py).
"""
from __future__ import annotations


# Local vs. international trip-type intent. Canonical source for
# validation_rules.py's TRIP_TYPE_ALIASES (re-exported there for backward
# compatibility).
TRIP_TYPE_TERMS: dict[str, str] = {
    "1": "local",
    "local": "local",
    "loc": "local",
    "loca": "local",
    "domestic": "local",
    "محلي": "local",
    "محليه": "local",
    "داخلي": "local",
    "داخليه": "local",
    "2": "international",
    "int": "international",
    "intl": "international",
    "international": "international",
    "inter": "international",
    "abroad": "international",
    "overseas": "international",
    "umrah": "international",
    "hajj": "international",
    "دولي": "international",
    "دوليه": "international",
    "خارجي": "international",
    "خارجيه": "international",
    "عمره": "international",
    "حج": "international",
    # How customers actually phrase it in Egyptian Arabic -- "outside/inside
    # Egypt" rather than the formal محلي/دولي. The prompt already promises
    # "بره" works, but collect_trip_type is a backend-owned step so only this
    # map is ever consulted.
    "بره": "international",
    "برة": "international",
    "برا": "international",
    "خارج مصر": "international",
    "بره مصر": "international",
    "برة مصر": "international",
    "outside egypt": "international",
    "جوه": "local",
    "جوة": "local",
    "جوا": "local",
    "داخل مصر": "local",
    "جوه مصر": "local",
    "جوة مصر": "local",
    "في مصر": "local",
    "inside egypt": "local",
    "in egypt": "local",
}

# A plain "yes"/confirm signal. Canonical source for
# tool_calling_runtime.py's _AFFIRMATIVE_REPLIES.
AFFIRMATIVE_TERMS: set[str] = {
    "yes",
    "y",
    "ok",
    "okay",
    "sure",
    "confirm",
    "book it",
    "go ahead",
    "تمام",
    "ماشي",
    "موافق",
    "ايوه",
    "أيوه",
    "نعم",
    "اوكي",
    "اوكى",
}

# A plain "no"/decline signal. Not extracted from an existing call site --
# no consolidated negative-term matcher exists yet anywhere in the runtime.
# Scaffolded here, inert (nothing reads it yet), so a future
# correction/negation check has one place to read from instead of inventing
# another private list. Zero behavior impact by construction.
NEGATIVE_TERMS: set[str] = {
    "no",
    "not",
    "لا",
    "مش",
    "ابدا",
    "أبدا",
}

# "Let me talk to a human" -- canonical source for
# tool_calling_runtime.py's _is_human_agent_request.
# NOTE: _is_human_agent_request matches these as plain SUBSTRINGS. Generic
# words like "help"/"support"/"مساعده" are intentionally kept, because a
# customer saying "I need support with my booking" or "محتاج مساعدة" really
# does want a human. The cost is that they also fire on the most natural
# sales question in the language ("can you help me choose?"), so that case is
# excluded separately via SELF_SERVICE_HELP_TERMS below rather than by
# weakening this set.
HUMAN_HANDOFF_TERMS: set[str] = {
    "human",
    "real agent",
    "real person",
    "human agent",
    "live agent",
    "person",
    "employee",
    "call me",
    "support",
    "help",
    "escalate",
    "speak to someone",
    "speak to a person",
    "speak to a human",
    "talk to someone",
    "talk to a human",
    "talk to a person",
    "transfer me",
    "connect me",
    # common misspellings of "escalate" seen live -- kept as an explicit
    # small list rather than fuzzy-matching, to avoid false positives
    # elsewhere in this substring check.
    "esclate",
    "excalate",
    "escallate",
    "موظف",
    "انسان",
    "كلمني",
    "دعم",
    # "help/support" (masa'ada), spelled with a plain ha: normalization
    # maps ta marbuta to ha before this check runs.
    "مساعده",
    "تصعيد",
    "حول لموظف",
    "عايز حد يرد",
    "حد حقيقي",
}

# Asking the AGENT ITSELF for help with a sales task. These override
# HUMAN_HANDOFF_TERMS in _is_human_agent_request: "can you help me choose?"
# and "عايز مساعدة في الاختيار" are the most common openers in this flow, and
# escalating them to a human ended the conversation before the agent ever got
# to sell anything. A genuine handoff request names a human ("موظف", "real
# agent", "talk to someone"), none of which appear here, so those still
# escalate normally.
SELF_SERVICE_HELP_TERMS: set[str] = {
    "can you help",
    "could you help",
    "would you help",
    "can u help",
    "you help me",
    "help me choose",
    "help me pick",
    "help me decide",
    "help me select",
    "help me find",
    "help me compare",
    "help me plan",
    "help me book",
    "تساعدني",
    "ساعدني",
    "مساعده في الاختيار",
    "مساعده فى الاختيار",
    "مساعده في اختيار",
}

# "Is this trip any good / worth it / would you recommend it." Canonical
# source for tool_calling_runtime.py's _is_trip_quality_question.
TRIP_QUALITY_TERMS: set[str] = {
    "is it good",
    "is this trip good",
    "worth it",
    "worth going",
    "recommend",
    "كويس",
    "كويسة",
    "يستاهل",
    "تنصح",
    "رأيك",
    "رايك",
    "عاجبتك",
    "حلوة",
}

# "What trips do you have" (a request for the list), as distinct from a
# lookup for one trip by name. Canonical source for
# tool_calling_runtime.py's _is_trip_discovery_request.
TRIP_DISCOVERY_TERMS: set[str] = {
    "what trips",
    "which trips",
    "any trips",
    "available trips",
    "trips available",
    "trips do you have",
    "trips you have",
    "show me trips",
    "show me the trips",
    "show trips",
    "list of trips",
    "what do you have",
    "what is available",
    "whats available",
    "where can i go",
    "what destinations",
    "ايه الرحلات",
    "اي الرحلات",
    "ايه رحلات",
    "ايه المتاح",
    "ايه الموجود",
    "في رحلات",
    "فيه رحلات",
    "هل يوجد رحلات",
    "يوجد رحلات",
    "الرحلات المتاحه",
    "الرحلات المتوفره",
    "رحلات متاحه",
    "رحلات متوفره",
    "عرض الرحلات",
    "وريني الرحلات",
    "ورينى الرحلات",
    "اعرض الرحلات",
    "ما الرحلات",
    "الرحلات المتاح",
    "عندكم رحلات",
    "عندكو رحلات",
    "الرحلات عندكم",
}

# "Why do you need that / what for" -- canonical source for
# tool_calling_runtime.py's _is_explanation_request. That function checks
# two DIFFERENT tiers (an exact-normalized-string match, then a separate
# exact-or-substring check) -- split into two sets here rather than one, so
# the extraction preserves that distinction exactly instead of silently
# widening the exact-only phrases to substring matching.
EXPLANATION_REQUEST_EXACT_TERMS: set[str] = {
    "what do i need",
    "what is needed",
    "يعني ايه",
    "يعني إيه",
    "وضح",
    "وضحلي",
    "ايه المطلوب",
    "why",
    "how come",
    "what for",
    "ليه",
    "لماذا",
    "ليش",
}
EXPLANATION_REQUEST_SUBSTRING_TERMS: set[str] = {
    "why do",
    "why are",
    "why need",
    "what do you need",
    "ليه",
    "لماذا",
    "ليش",
    "عشان ايه",
    "ليه محتاج",
}

# An unambiguous "I changed my mind, start over" signal -- canonical source
# for tool_calling_runtime.py's _is_explicit_trip_type_restart_signal, and
# the shared marker set for the trip-switch correction check (Phase 2).
RESTART_SIGNAL_TERMS: set[str] = {
    "instead",
    "actually",
    "بدل",
    "خليها",
    "غير رأيي",
    "عايز اغير",
    "عايزة اغير",
}

# Phase 11: an explicit "I don't want this trip, show me something else"
# signal that names no specific alternate trip -- distinct from
# RESTART_SIGNAL_TERMS (which requires an actual replacement value/trip to
# already be given alongside it) and from TRIP_DISCOVERY_TERMS (a neutral
# "what do you have" question, not a rejection of the current selection).
# Matched with substring-in-normalized-text, same convention as
# TRIP_DISCOVERY_TERMS -- entries are pre-normalized (bare alef, ه for tied
# taa marbuta) to match _normalize_trip_reference's own output.
GENERIC_TRIP_CHANGE_TERMS: set[str] = {
    "رحله تانيه",
    "رحله ثانيه",
    "رحلات تانيه",
    "رحلات ثانيه",
    "رحله اخرى",
    "رحله غير",
    "اغير الرحله",
    "تغيير الرحله",
    "غير الرحله",
    "مش عايز الرحله",
    "ما عايز الرحله",
    "مش عايزه الرحله",
    "عايز رحله غير",
    "عايزه رحله غير",
    "اختارلي رحله غير",
    "اختاري رحله غير",
    "another trip",
    "a different trip",
    "different trip instead",
    "change the trip",
    "change my trip",
    "not this trip",
}
