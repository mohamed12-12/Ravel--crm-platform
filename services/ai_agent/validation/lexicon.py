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
    # "I need to know the trips [first]" -- a general discovery question, not
    # a specific trip name. Missing entirely used to make _is_trip_discovery_
    # request return False, so _has_trip_reference_words' bare "رحلات" match
    # fell through to the "trip not found" reply as if this were a failed
    # name lookup.
    "اعرف الرحلات",
    "أعرف الرحلات",
    "عايز اعرف الرحلات",
    "عارف الرحلات",
    "معرفة الرحلات",
    "اعرف عن الرحلات",
    "اعرف انواع الرحلات",
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
    # Field-capture audit: a customer asking "what do you mean?" in any of
    # these phrasings was previously only caught by _is_exploratory_question
    # (hypothetical-only) or not at all -- "يعني ايه" was already here, but
    # "زي ايه"/"مثل ايه"/"تقصد ايه" are the same clarification request in
    # other common phrasings and were missing.
    "زي ايه",
    "زي إيه",
    "مثل ايه",
    "مثل إيه",
    "تقصد ايه",
    "تقصد إيه",
}
# "ليه" ("why") is deliberately NOT in this set even though it reads like a
# natural fit: as a blind substring it also matches inside "خليها" (make
# it), "عليها"/"عليه" (on it), and "إليها"/"إليه" (to it) -- all extremely
# common words, none of them an explanation request. Semantic-capture audit
# phase 1 (PC-1 investigation) found this swallowing trip-type/field
# corrections phrased with "خليها ..." into a generic "let me explain"
# reply before the classifier ever ran. tool_calling_runtime.py's
# _is_explanation_request checks it separately with a real word boundary
# instead of the plain substring test every other term here uses.
EXPLANATION_REQUEST_SUBSTRING_TERMS: set[str] = {
    "why do",
    "why are",
    "why need",
    "what do you need",
    "لماذا",
    "ليش",
    "عشان ايه",
    "ليه محتاج",
    "what do you mean",
    "what does that mean",
    "can you explain",
    "like what",
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

# A customer names a specific destination instead of answering the abstract
# collect_trip_type question ("local inside Egypt or international outside
# Egypt?") -- e.g. "شرم" / "شرم الشيخ" for Sharm El Sheikh. Without this,
# _apply_required_step_capture's trip_type_required branch (
# tool_calling_runtime.py) had no fallback beyond TRIP_TYPE_TERMS's generic
# local/international words, so naming a real place repeatedly re-asked the
# same menu question and eventually escalated the customer to a human for
# giving "unclear" answers -- even though they had already answered, just
# not in the local/international vocabulary the capture function expected.
# Matched as a substring against normalized (casefolded) text, same
# convention as TRIP_DISCOVERY_TERMS et al. -- longer/more specific keys are
# checked first by the lookup helper so "شرم الشيخ" doesn't get shadowed by
# a shorter unrelated key. Values are (trip_type, canonical destination name
# used for trip_query/search -- the same field _merge_hints's
# candidate_destination hint already writes to).
DESTINATION_TRIP_TYPE_ALIASES: dict[str, tuple[str, str]] = {
    "شرم الشيخ": ("local", "Sharm El Sheikh"),
    "شرم الشيخه": ("local", "Sharm El Sheikh"),
    "شرم": ("local", "Sharm El Sheikh"),
    "sharm el sheikh": ("local", "Sharm El Sheikh"),
    "sharm": ("local", "Sharm El Sheikh"),
    "الغردقة": ("local", "Hurghada"),
    "الغردقه": ("local", "Hurghada"),
    "hurghada": ("local", "Hurghada"),
    "دهب": ("local", "Dahab"),
    "dahab": ("local", "Dahab"),
    "نويبع": ("local", "Nuweiba"),
    "nuweiba": ("local", "Nuweiba"),
    "رأس سدر": ("local", "Ras Sudr"),
    "راس سدر": ("local", "Ras Sudr"),
    "ras sudr": ("local", "Ras Sudr"),
    "مرسى علم": ("local", "Marsa Alam"),
    "مرسي علم": ("local", "Marsa Alam"),
    "marsa alam": ("local", "Marsa Alam"),
    "سيوة": ("local", "Siwa"),
    "سيوه": ("local", "Siwa"),
    "siwa": ("local", "Siwa"),
    "الفيوم": ("local", "Fayoum"),
    "fayoum": ("local", "Fayoum"),
    "العلمين": ("local", "El Alamein"),
    "el alamein": ("local", "El Alamein"),
    "الأقصر": ("local", "Luxor"),
    "الاقصر": ("local", "Luxor"),
    "luxor": ("local", "Luxor"),
    "أسوان": ("local", "Aswan"),
    "اسوان": ("local", "Aswan"),
    "aswan": ("local", "Aswan"),
    "تركيا": ("international", "Turkey"),
    "turkey": ("international", "Turkey"),
    "دبي": ("international", "Dubai"),
    "dubai": ("international", "Dubai"),
    "جورجيا": ("international", "Georgia"),
    "georgia": ("international", "Georgia"),
    "المالديف": ("international", "Maldives"),
    "maldives": ("international", "Maldives"),
    "زنجبار": ("international", "Zanzibar"),
    "zanzibar": ("international", "Zanzibar"),
    "لبنان": ("international", "Lebanon"),
    "lebanon": ("international", "Lebanon"),
}

# "Is that the only one? / nothing else?" -- a direct question about the
# result count, asked right after the agent lists the currently-available
# trip(s) (see ToolCallingSessionRuntime._is_only_option_question). Without
# recognizing this, the customer's own question was misread as an unparsed
# attempt to pick a trip, incrementing unclear_step_strikes and eventually
# either repeating the same list or escalating for the wrong reason.
ONLY_OPTION_QUESTION_TERMS: set[str] = {
    "مفيش غير",
    "مافيش غير",
    "غير ديه",
    "غير ده",
    "غير دي",
    "غير كده",
    "بس كده",
    "دي بس",
    "ده بس",
    "مفيش تانى",
    "مفيش تاني",
    "مفيش حاجه تانيه",
    "مفيش حاجة تانية",
    "is that all",
    "is this the only",
    "is that the only",
    "anything else",
    "any other options",
    "any other trips",
    "nothing else",
    "no other trips",
    "no other options",
}

# Field-capture audit: a numbered-choice question ("1. X / 2. Y / 3. Z")
# only ever recognized a BARE digit ("3") -- "رقم 3"/"اختيار 3"/"option 3"
# and ordinal words ("التالت"/"third") fell through as unparsed, forcing the
# customer to re-answer with a naked digit even though the intent was
# perfectly clear the first time. Canonical source for
# ToolCallingSessionRuntime._extract_option_number, used only by the
# genuinely fixed-menu fields (trip type, service type, room type, flight
# option, currency, gender, group nationality type, duplicate-lead choice,
# trip selection) -- deliberately NOT used by any "how many" free-number
# field (group size, party size), where a bare 3 already means "3 people"
# and an ordinal word has no sensible meaning.
OPTION_NUMBER_PREFIX_TERMS: set[str] = {
    "رقم",
    "اختيار",
    "الاختيار",
    "الخيار",
    "خيار",
    "option",
    "choice",
}
# "Do you people actually DO this?" -- a question about what Ravel offers as a
# company, asked mid-flow instead of answering the pending required step. Live
# transcript (2026-08-20): a customer mid name-collection asked
# "هو انتم بتنظموا رحلات خاصه؟" and got the name question repeated back
# verbatim, because no existing predicate recognized it: it is not an
# EXPLANATION_REQUEST ("why do you need that?"), not a TRIP_QUALITY question
# ("is this trip good?"), and not a TRIP_DISCOVERY request ("what trips do you
# have?") -- it asks whether a whole SERVICE EXISTS. Canonical source for
# ToolCallingSessionRuntime._is_service_capability_question, matched as a
# substring against _normalize_trip_reference output, so entries are
# pre-normalized (bare alef, ه for tied taa marbuta, no punctuation).
SERVICE_CAPABILITY_QUESTION_TERMS: set[str] = {
    "do you organize",
    "do you organise",
    "do you arrange",
    "do you offer",
    "do you provide",
    "do you handle",
    "do you also do",
    "can you organize",
    "can you organise",
    "can you arrange",
    "is that something you do",
    "do you guys do",
    "بتنظموا",
    "بتنظمون",
    "تنظموا",
    "تنظمون",
    "بتعملوا",
    "بتعملون",
    "بتوفروا",
    "بتوفرون",
    "بتصمموا",
    "بتظبطوا",
    "بتساعدوا",
    "هو انتم",
    "هو انتو",
    "انتم بتعملوا",
    "انتو بتعملوا",
    # Deliberately NOT "عندكم رحلات"/"عندكو رحلات": TRIP_DISCOVERY_TERMS above
    # already owns those, and "do you have trips?" wants the trip LIST, not a
    # description of the company. Only the phrasing discovery does not cover is
    # listed here.
    "عندكم برامج",
    "ممكن تنظموا",
    "ممكن تعملوا",
}

# "Where did my request go?" -- a status question about an ALREADY-submitted
# request, as distinct from _post_booking_status_intent's existing
# booking-shaped vocabulary ("حالة الحجز", "booking status"). Live transcript
# (2026-08-20): right after a private trip request was submitted, "طلبي فين"
# matched none of the booking terms and fell through to the generic
# "I need one more detail" non-answer. Canonical source for
# ToolCallingSessionRuntime._post_booking_status_intent's request-shaped tier.
# Matched against casefolded, whitespace-collapsed text (that function's own
# convention), NOT against _normalize_trip_reference output -- so entries keep
# their natural taa marbuta spelling and both hamza forms are listed.
REQUEST_STATUS_QUESTION_TERMS: set[str] = {
    "طلبي فين",
    "فين طلبي",
    "طلبي وصل",
    "وصل طلبي",
    "حالة طلبي",
    "حاله طلبي",
    "ايه اخبار طلبي",
    "إيه اخبار طلبي",
    "اخبار طلبي",
    "طلبي اتسجل",
    "الطلب فين",
    "فين الطلب",
    "حالة الطلب",
    "حاله الطلب",
    "where is my request",
    "where's my request",
    "my request status",
    "status of my request",
    "request status",
    "was my request created",
    "did my request go through",
}

OPTION_ORDINAL_TERMS: dict[str, int] = {
    "الاول": 1,
    "الأول": 1,
    "اول": 1,
    "أول": 1,
    "first": 1,
    "الثاني": 2,
    "الثانى": 2,
    "التاني": 2,
    "ثاني": 2,
    "تاني": 2,
    "second": 2,
    "الثالث": 3,
    "التالت": 3,
    "ثالث": 3,
    "تالت": 3,
    "third": 3,
    "الرابع": 4,
    "رابع": 4,
    "fourth": 4,
    "الخامس": 5,
    "خامس": 5,
    "fifth": 5,
}
