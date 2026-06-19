# Traveler Reference Recovery

Scope: analysis only. No traveler rows were imported, merged, edited, or assigned new IDs.

Source inspected:

- Quarantine file: `.tmp-booking-import-populated/quarantine.json`
- CRM database used for analysis: `.tmp-booking-import-populated/crm.db`
- Workbook sheet: `Travelers`
- Quarantine reason: `invalid_booking_traveler_reference`
- Invalid booking traveler references reviewed: 36

## Recovery Policy

Phone number has highest weight. Email has second highest weight. Name-only matching is not allowed.

For all 36 invalid booking traveler references, there was no safe phone or email match to exactly one imported CRM traveler. Therefore all remain manual review cases.

## Grouped Recovery Review

| Booking Row(s) | Booking ID(s) | Workbook Traveler ID | Traveler Name | Proposed CRM Traveler | Confidence | Reason |
|---|---|---|---|---|---|---|
| 343 | `0-L25003-013` | blank | Nada Maged | None | Low | Missing Traveler ID and no safe phone/email evidence in the booking row. Name-only match is not allowed. |
| 81 | `73-I25002-008` | `TR00073` | Abdulaziz Alnawfal | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 104 | `94-L24005-017` | `TR00094` | Mohammed Riyati | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 105 | `95-L24005-018` | `TR00095` | Mohammed Nabil | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 122 | `107-L24006-009` | `TR00107` | Eslam island hopping | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 151 | `130-I25003-013` | `TR00130` | Samir hashem | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 152 | `131-I25003-014` | `TR00131` | Mostafa Bassel | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 178 | `146-L24008-002` | `TR00146` | Soumaya | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 193 | `161-L24008-017` | `TR00161` | Rana Mohamed | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 194 | `162-L24008-018` | `TR00162` | Rasha Bikeer | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 214 | `175-I25004-001` | `TR00175` | Mustafa basil | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 215 | `176-I25004-002` | `TR00176` | Saria | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 273 | `229-L24012-021` | `TR00229` | Rania's Child (5 years) | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 274 | `230-L24012-022` | `TR00230` | Abdelrahman Othman | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 278 | `234-L24012-026` | `TR00234` | Fernando Revelo | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 346, 450, 476 | `294-I25001-001`; `294-I25003-024`; `294-I25006-005` | `TR00294` | Mohamed Fawzy / Mohamed fawzy | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. Repeated booking references do not prove identity. |
| 406 | `348-I25002-026` | `TR00348` | Abdel Aziz Abn Mohamed | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 410 | `352-L25006-002` | `TR00352` | Islam mohamed | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 412 | `354-L25006-004` | `TR00354` | Ahmed Naim | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 435 | `376-L25008-001` | `TR00376` | Ahmed Ayman | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 453 | `390-L25009-001` | `TR00390` | Abdelrahman Salah | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 478 | `410-L25010-001` | `TR00410` | Abdulrahma Ashur | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 484, 547, 575 | `416-L25010-007`; `416-L26003-007`; `416-L26006-005` | `TR00416` | Amr hafez | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. Repeated booking references do not prove identity. |
| 494 | `426-L26001-001` | `TR00426` | Islam Medhat | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 496 | `428-L26001-003` | `TR00428` | Ahmed Negida | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 498, 619 | `430-L26001-005`; `430-L26021-001` | `TR00430` | Mohamed Rizk / mohamed rizk | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. Repeated booking references do not prove identity. |
| 503 | `435-L26001-010` | `TR00435` | Mohamed Rawash | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 520 | `451-L26002-006` | `TR00451` | Mona's daughter^ | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 573 | `501-L26006-003` | `TR00501` | Arwa El Rouby | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 592 | `518-I26005-002` | `TR00518` | Noura Mohamed Zuhair | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |
| 626 | `557-I26006-003` | `TR00557` | Samer Riad | None | Low | Referenced Traveler ID is not present in imported CRM and no safe phone/email match was available. |

## Result

- High confidence recovered traveler references: 0
- Medium confidence references: 0
- Low confidence/manual review references: 36

## CTO Decision

Do not recover these traveler references automatically. Manual review must provide phone, WhatsApp, email, or a confirmed CRM Traveler ID before any booking import can use these rows.

