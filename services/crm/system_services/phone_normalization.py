from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

COUNTRY_RULES: list[tuple[str, str, tuple[str, ...]]] = [
    ("966", "Saudi Arabia", ("5",)),
    ("971", "United Arab Emirates", ("5", "50", "52", "54", "55", "56", "58")),
    ("965", "Kuwait", ("5", "6", "9")),
    ("974", "Qatar", ("3", "5", "6", "7")),
    ("973", "Bahrain", ("3", "6", "7")),
    ("968", "Oman", ("7", "9")),
    ("962", "Jordan", ("7",)),
    ("961", "Lebanon", ("3", "7", "8", "9")),
    ("44", "United Kingdom", tuple()),
    ("1", "United States/Canada", tuple()),
    ("20", "Egypt", ("10", "11", "12", "15")),
]

_COUNTRY_NAMES = {code: name for code, name, _ in COUNTRY_RULES}
_COUNTRY_PREFIXES = {code: prefixes for code, _name, prefixes in COUNTRY_RULES}
_LOCAL_EGYPT_PREFIXES = {"10", "11", "12", "15"}
_KNOWN_CODES_DESC = sorted(_COUNTRY_NAMES, key=len, reverse=True)
_LOCAL_LENGTH_RULES: dict[str, set[int]] = {
    "1": {10},
    "20": {10},
    "44": {10},
    "961": {7, 8},
    "962": {9},
    "965": {8},
    "966": {9},
    "968": {8},
    "971": {9},
    "973": {8},
    "974": {8},
}
_MIN_E164_DIGITS = 8
_MAX_E164_DIGITS = 15
_MIN_LOCAL_DIGITS = 6
_MAX_LOCAL_DIGITS = 12


@dataclass(frozen=True)
class PhoneNormalizationResult:
    raw_input: str
    normalized_e164: str
    country_code: str
    local_number: str
    phone_variants_for_lookup: list[str]
    confidence: str
    requires_country_confirmation: bool
    inferred_country: str
    inferred_nationality: str
    is_valid: bool
    invalid_reason: str

    def to_dict(self) -> dict[str, Any]:
        lookup_key = next((v for v in self.phone_variants_for_lookup if ":" in v), "")
        digits_only = re.sub(r"\D", "", self.normalized_e164)
        return {
            "raw_input": self.raw_input,
            "raw_phone": self.raw_input,
            "phone": self.raw_input,
            "normalized_e164": self.normalized_e164,
            "normalized_whatsapp": self.normalized_e164,
            "e164": self.normalized_e164,
            "country_code": self.country_code,
            "local_number": self.local_number,
            "phone_variants_for_lookup": self.phone_variants_for_lookup,
            "legacy_lookup_keys": self.phone_variants_for_lookup,
            "lookup_key": lookup_key,
            "phone_lookup_key": lookup_key,
            "digits_only": digits_only,
            "confidence": self.confidence,
            "requires_country_confirmation": self.requires_country_confirmation,
            "requires_country_code_confirmation": self.requires_country_confirmation,
            "inferred_country": self.inferred_country,
            "inferred_nationality": self.inferred_nationality,
            "is_valid": self.is_valid,
            "invalid_reason": self.invalid_reason,
            "country_hint": self.inferred_country,
            "nationality_hint": self.inferred_nationality,
        }


def _clean_phone_text(raw_phone: str) -> str:
    return re.sub(r"[\s\-().]", "", (raw_phone or "").split("@", 1)[0].strip())


def _prefix_matches(local_number: str, prefixes: tuple[str, ...]) -> bool:
    return any(local_number.startswith(prefix) for prefix in prefixes)


def _validate_phone_shape(
    *,
    digits: str,
    country_code: str,
    local_number: str,
    requires_country_confirmation: bool,
    enforce_prefix_validation: bool,
) -> tuple[bool, str]:
    if not digits:
        return False, "missing_digits"
    if len(digits) > _MAX_E164_DIGITS:
        return False, "too_many_digits"
    if len(digits) < _MIN_E164_DIGITS:
        return False, "too_few_digits"
    if requires_country_confirmation:
        if digits.startswith("0") and len(digits) > 11:
            return False, "invalid_local_length"
        if len(digits) > 12:
            return False, "invalid_local_length"
        return True, ""
    if local_number and (len(local_number) < _MIN_LOCAL_DIGITS or len(local_number) > _MAX_LOCAL_DIGITS):
        return False, "invalid_local_length"
    allowed_lengths = _LOCAL_LENGTH_RULES.get(country_code)
    if allowed_lengths and local_number and len(local_number) not in allowed_lengths:
        return False, "invalid_local_length"
    expected_prefixes = _COUNTRY_PREFIXES.get(country_code, ())
    if enforce_prefix_validation and expected_prefixes and local_number and not _prefix_matches(local_number, expected_prefixes):
        return False, "invalid_mobile_prefix"
    return True, ""


def _detect_country_code_from_plus(digits: str) -> tuple[str, str] | tuple[str, str, str]:
    for code in _KNOWN_CODES_DESC:
        if digits.startswith(code):
            local_number = digits[len(code) :]
            if not local_number:
                return ("", "", "")
            return (code, local_number)
    return ("", "")


def normalize_phone_input(
    raw_phone: str,
    default_country_code: str = "",
    *,
    default_country_is_explicit: bool = False,
) -> PhoneNormalizationResult:
    raw_input = (raw_phone or "").strip()
    cleaned = _clean_phone_text(raw_input)
    digits = re.sub(r"\D", "", cleaned)
    normalized_e164 = ""
    country_code = ""
    local_number = ""
    confidence = "low"
    requires_country_confirmation = False
    inferred_country = ""
    inferred_nationality = ""
    plus_prefixed = cleaned.startswith("+")
    enforce_prefix_validation = False

    if not digits:
        return PhoneNormalizationResult(
            raw_input=raw_input,
            normalized_e164="",
            country_code="",
            local_number="",
            phone_variants_for_lookup=[],
            confidence="low",
            requires_country_confirmation=True,
            inferred_country="",
            inferred_nationality="",
            is_valid=False,
            invalid_reason="missing_digits",
        )

    if cleaned.startswith("00"):
        plus_prefixed = True
        digits = digits[2:] if digits.startswith("00") else digits
        cleaned = "+" + digits

    if default_country_code and default_country_is_explicit and not plus_prefixed:
        country_code = default_country_code
        if digits.startswith(country_code) and len(digits) > len(country_code):
            local_number = digits[len(country_code) :]
            enforce_prefix_validation = True
        elif country_code == "20" and digits.startswith("0") and len(digits) == 11 and _prefix_matches(digits[1:], _LOCAL_EGYPT_PREFIXES):
            local_number = digits[1:]
            enforce_prefix_validation = True
        elif country_code == "20" and len(digits) == 10 and _prefix_matches(digits, _LOCAL_EGYPT_PREFIXES):
            local_number = digits
            enforce_prefix_validation = True
        else:
            local_number = digits[1:] if country_code == "20" and digits.startswith("0") else digits
        normalized_e164 = f"+{country_code}{local_number}" if country_code and local_number else ""
        confidence = "high" if country_code in _COUNTRY_NAMES else "medium"
        inferred_country = _COUNTRY_NAMES.get(country_code, "")
        if country_code == "20" and _prefix_matches(local_number, _LOCAL_EGYPT_PREFIXES):
            inferred_nationality = "Egyptian"
        requires_country_confirmation = not bool(country_code and local_number)
    elif plus_prefixed:
        detected_code = ""
        detected_local = ""
        for code in _KNOWN_CODES_DESC:
            if digits.startswith(code):
                detected_code = code
                detected_local = digits[len(code) :]
                break
        if detected_code and detected_local:
            country_code = detected_code
            local_number = detected_local
            normalized_e164 = f"+{digits}"
            enforce_prefix_validation = True
            confidence = "high"
            inferred_country = _COUNTRY_NAMES.get(country_code, "")
            if country_code == "20" and _prefix_matches(local_number, _LOCAL_EGYPT_PREFIXES):
                inferred_nationality = "Egyptian"
        else:
            requires_country_confirmation = True
    else:
        if digits.startswith("20") and len(digits) > 10:
            country_code = "20"
            local_number = digits[2:]
            normalized_e164 = f"+{country_code}{local_number}"
            enforce_prefix_validation = True
            confidence = "high"
            inferred_country = "Egypt"
            inferred_nationality = "Egyptian" if _prefix_matches(local_number, _LOCAL_EGYPT_PREFIXES) else ""
        elif digits.startswith(("966", "971", "965", "974", "973", "968", "962", "961", "44", "1")):
            for code in _KNOWN_CODES_DESC:
                if digits.startswith(code):
                    local_number = digits[len(code) :]
                    if local_number:
                        country_code = code
                        normalized_e164 = f"+{country_code}{local_number}"
                        enforce_prefix_validation = True
                        confidence = "high"
                        inferred_country = _COUNTRY_NAMES.get(country_code, "")
                    break
            if not country_code:
                requires_country_confirmation = True
        elif default_country_code and default_country_is_explicit:
            if digits.startswith("0") and default_country_code == "20" and len(digits) == 11 and _prefix_matches(digits[1:], _LOCAL_EGYPT_PREFIXES):
                local_number = digits[1:]
            else:
                local_number = digits[1:] if digits.startswith("0") else digits
            country_code = default_country_code
            normalized_e164 = f"+{country_code}{local_number}"
            confidence = "high" if default_country_code in _COUNTRY_NAMES else "medium"
            inferred_country = _COUNTRY_NAMES.get(country_code, "")
            if country_code == "20" and _prefix_matches(local_number, _LOCAL_EGYPT_PREFIXES):
                inferred_nationality = "Egyptian"
        else:
            requires_country_confirmation = True

    variants: list[str] = []
    if normalized_e164:
        variants.append(normalized_e164)
        digits_only = re.sub(r"\D", "", normalized_e164)
        variants.append(digits_only)
        if country_code and local_number:
            variants.append(f"{country_code}:{local_number}")
            variants.append(local_number)
            variants.append(f"+{digits_only}")
            variants.append(f"{country_code}{local_number}")
    elif digits:
        variants.append(digits)
    variants.append(raw_input)
    variants = list(dict.fromkeys(v for v in variants if v))

    if requires_country_confirmation:
        confidence = "low"
        country_code = country_code or ""
        local_number = local_number or ""
        normalized_e164 = normalized_e164 or ""
        inferred_country = ""
        inferred_nationality = ""

    is_valid, invalid_reason = _validate_phone_shape(
        digits=digits,
        country_code=country_code,
        local_number=local_number,
        requires_country_confirmation=requires_country_confirmation,
        enforce_prefix_validation=enforce_prefix_validation,
    )
    if not is_valid:
        normalized_e164 = ""
        inferred_country = ""
        inferred_nationality = ""
        confidence = "low"
        if invalid_reason != "missing_digits":
            requires_country_confirmation = False

    return PhoneNormalizationResult(
        raw_input=raw_input,
        normalized_e164=normalized_e164,
        country_code=country_code,
        local_number=local_number,
        phone_variants_for_lookup=variants,
        confidence=confidence,
        requires_country_confirmation=requires_country_confirmation,
        inferred_country=inferred_country,
        inferred_nationality=inferred_nationality,
        is_valid=is_valid,
        invalid_reason=invalid_reason,
    )
