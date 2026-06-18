# Duplicate Traveler Review

Source analyzed:

- `RT - Travelers Database.xlsx`
- Traveler rows flagged by duplicate traveler ID or duplicate phone identity

Rules applied:

- Phone number had highest weight.
- Email had second-highest weight.
- Traveler ID alone was not treated as proof.
- Name similarity by itself was not enough.
- If confidence was not high, the group was marked for review.

## SAME_PERSON_HIGH_CONFIDENCE

### Group ID: `20.0:1004006276`
Evidence: Rows 356 and 377 both resolve to the same phone identity, both are `Ahmed Ayman`, and the only difference is spacing/trailing whitespace in the name and phone formatting.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1099911488`
Evidence: Rows 157 and 163 both have the same phone identity and the same name `Rasha Bikeer`. One row has email and birthday filled, the other is mostly blank, which looks like an incomplete duplicate rather than a different person.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `974.0:55171371`
Evidence: Rows 111 and 131 share the same phone identity and the same name `Samir hashem`. One row contains email and birthday while the other is blank in those fields.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1221328311`
Evidence: Rows 495 and 545 share the same phone identity and the same name `Mahmoud Awed`. The second row only changes spacing in the phone value.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1225734868`
Evidence: Rows 493 and 546 share the same phone identity and the same name `Shorouk Hamzawy`. The duplicate is only formatting noise.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `49.0:1734781673`
Evidence: Rows 472 and 521 share the same phone identity and the same full name `Kathrina Kalbitz`. The second row only differs by spacing in the phone value.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1033353567`
Evidence: Rows 141 and 411 share the same phone identity. The names are `Abdulrahman Ashour` and `Abdulrahman Ashur`, which is a small spelling variation. The first row also has email and birthday.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1016324366`
Evidence: Rows 139 and 147 share the same phone identity. The names `Somaia Ahmed` and `Soumaya` look like the same person with a spelling/partial-name variation. The first row also has email, birthday, and residence.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1277445335`
Evidence: Rows 41 and 355 share the same phone identity and both names are `Ahmed naim` / `Ahmed Naim`. This is a straightforward capitalization and spacing duplicate.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1065277121`
Evidence: Rows 169 and 391 share the same phone identity. The names `AbdelRahman Salah Mohamed` and `Abdelrahman Salah` are very close, and the first row has email and birthday.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

### Group ID: `20.0:1096833188`
Evidence: Rows 156 and 162 share the same phone identity and the same name `Rana Mohamed`. The second row is just a sparse duplicate.
Confidence: High
Classification: SAME_PERSON_HIGH_CONFIDENCE
Recommended Action: Merge after review

## SAME_PERSON_MEDIUM_CONFIDENCE

### Group ID: `966.0:554421909`
Evidence: Rows 74 and 349 share the same phone identity. The names `Abdulaziz Alnawfal` and `Abdel Aziz Abn Mohamed` are not identical, but they are plausibly transliteration variants. The first row has email and residence, which makes it more likely one person.
Confidence: Medium
Classification: SAME_PERSON_MEDIUM_CONFIDENCE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `1.0:8722287794`
Evidence: Rows 235 and 530 share the same phone identity. Both names start with `Fernando`, but one has the full surname `Revelo` and the other is truncated to `Fernando`. No email is available to confirm.
Confidence: Medium
Classification: SAME_PERSON_MEDIUM_CONFIDENCE
Recommended Action: Manual review required

## POSSIBLE_DUPLICATE_NEEDS_REVIEW

### Group ID: `971.0:563730780`
Evidence: Rows 132, 176, and 177 share the same phone identity. Rows 132 and 176 look like the same person (`Mostafa Bassel` / `Mustafa basil`) with transliteration differences, but row 177 is `Saria`, which does not match the other two. The group is mixed and cannot be merged safely as-is.
Confidence: Medium
Classification: POSSIBLE_DUPLICATE_NEEDS_REVIEW
Recommended Action: Manual review required

### Group ID: `20.0:1159280528`
Evidence: Rows 95 and 108 share the same phone identity, but the names are `Mohammed Riyati` and `Eslam island hopping`. That is too different to treat as the same person automatically, even though one row has email.
Confidence: Medium
Classification: POSSIBLE_DUPLICATE_NEEDS_REVIEW
Recommended Action: Manual review required

### Group ID: `20.0:1000969611`
Evidence: Rows 96 and 231 share the same phone identity, but the names are `Mohammed Nabil` and `Abdelrahman Othman`. The phone collision is real, but there is not enough evidence to say they are the same person.
Confidence: Medium
Classification: POSSIBLE_DUPLICATE_NEEDS_REVIEW
Recommended Action: Manual review required

### Group ID: `20.0:1069977005`
Evidence: Rows 353 and 427 share the same phone identity, but the names are `Islam mohamed` and `Islam Medhat`. The first name matches, the surnames do not, and only one row has a birthday.
Confidence: Medium
Classification: POSSIBLE_DUPLICATE_NEEDS_REVIEW
Recommended Action: Manual review required

### Group ID: `20.0:1055756130`
Evidence: Rows 417 and 478 look like the same person (`Amr hafez` / `Amr Hafez`), but row 502 is `Arwa El Rouby`, which is clearly a different name on the same phone identity. This is a mixed group, so it must be reviewed before any merge.
Confidence: Medium
Classification: POSSIBLE_DUPLICATE_NEEDS_REVIEW
Recommended Action: Manual review required

## DIFFERENT_PEOPLE

### Group ID: `TR00397`
Evidence: Two different traveler records share the same Traveler ID. Row 398 is `Mohamed elmahdy` with Egyptian phone identity, while row 519 is `Noura Mohamed Zuhair` with a Syrian phone identity and a completely different contact profile.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Assign new Traveler ID

### Group ID: `TR00398`
Evidence: Two different traveler records share the same Traveler ID. Row 399 is `Mouna elkhat` with a Swiss phone identity, while row 520 is `Marwa Abouelnasr` with a US phone identity and different nationality.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Assign new Traveler ID

### Group ID: `20.0:1060035570`
Evidence: Rows 500 and 522 share the same phone identity, but the names `Ahmed Essam` and `May Sherif` are unrelated.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `20.0:1097850042`
Evidence: Rows 451 and 452 share the same phone identity, but the names `Mona Hamaki` and `Mona's daughter^` indicate two different people, likely a parent and child.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `20.0:1127311373`
Evidence: Rows 229 and 230 share the same phone identity, but the names `Rania Ali +` and `Rania's Child (5 years)` are clearly different people.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `20.0:12006444000`
Evidence: Rows 571 and 572 share the same phone identity, but the names `Mervat Fawzy` and `Martina Waleed` are unrelated.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `20.0:1280003592`
Evidence: Rows 331 and 429 share the same phone identity, but the names `Ahmed Yassin Hassan` and `Ahmed Negida` are different people.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

### Group ID: `20.0:546052012`
Evidence: Rows 227 and 436 share the same phone identity, but the names `Mohamed ElSayed Amin` and `Mohamed Rawash` are unrelated.
Confidence: High
Classification: DIFFERENT_PEOPLE
Recommended Action: Keep primary record and archive duplicate

## Summary

- High confidence duplicates: 11
- Medium confidence duplicates: 2
- Manual review cases: 5
- Different people: 8

## Decision Rule

Only the `SAME_PERSON_HIGH_CONFIDENCE` groups should be considered safe candidates for automated cleanup.

Everything else should stay under manual review or remain separate until a human confirms the match.
