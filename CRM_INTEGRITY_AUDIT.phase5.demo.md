# CRM Integrity Audit

Generated: 2026-05-13T14:05:01

## Identity Summary

- Travelers rows scanned: 775
- Valid Traveler IDs: 522
- Invalid Traveler IDs: 0
- True max Traveler ID: `TR00522` at row `523`
- Next Traveler ID: `TR00523`
- Duplicate Traveler ID keys: 0
- Duplicate phone lookup keys: 18
- Rows with ID but missing name or phone: 95
- Rows with phone but missing lookup key: 0
- Hidden rows with data: 4
- Auto filter range: ``

## Rule

Do not use the last visible Google Sheet row for IDs. Scan the full Travelers sheet.

## Issue Sample

- `Critical` `Travelers` row `96` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00095` `Mohammed Nabil` `20:1000969611` - 20:1000969611 appears in rows 96, 231
- `Critical` `Travelers` row `231` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00230` `Abdelrahman Othman` `20:1000969611` - 20:1000969611 appears in rows 96, 231
- `Critical` `Travelers` row `356` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00355` `Ahmed Ayman` `20:1004006276` - 20:1004006276 appears in rows 356, 377
- `Critical` `Travelers` row `377` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00376` `Ahmed Ayman` `20:1004006276` - 20:1004006276 appears in rows 356, 377
- `Critical` `Travelers` row `139` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00138` `Somaia Ahmed` `20:1016324366` - 20:1016324366 appears in rows 139, 147
- `Critical` `Travelers` row `147` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00146` `Soumaya` `20:1016324366` - 20:1016324366 appears in rows 139, 147
- `Critical` `Travelers` row `141` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00140` `Abdulrahman Ashour` `20:1033353567` - 20:1033353567 appears in rows 141, 411
- `Critical` `Travelers` row `411` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00410` `Abdulrahman Ashur` `20:1033353567` - 20:1033353567 appears in rows 141, 411
- `Critical` `Travelers` row `417` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00416` `Amr hafez` `20:1055756130` - 20:1055756130 appears in rows 417, 478
- `Critical` `Travelers` row `478` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00477` `Amr Hafez` `20:1055756130` - 20:1055756130 appears in rows 417, 478
- `Critical` `Travelers` row `169` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00168` `AbdelRahman Salah Mohamed` `20:1065277121` - 20:1065277121 appears in rows 169, 391
- `Critical` `Travelers` row `391` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00390` `Abdelrahman Salah` `20:1065277121` - 20:1065277121 appears in rows 169, 391
- `Critical` `Travelers` row `353` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00352` `Islam mohamed` `20:1069977005` - 20:1069977005 appears in rows 353, 427
- `Critical` `Travelers` row `427` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00426` `Islam Medhat` `20:1069977005` - 20:1069977005 appears in rows 353, 427
- `Critical` `Travelers` row `156` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00155` `Rana Mohamed` `20:1096833188` - 20:1096833188 appears in rows 156, 162
- `Critical` `Travelers` row `162` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00161` `Rana Mohamed` `20:1096833188` - 20:1096833188 appears in rows 156, 162
- `Critical` `Travelers` row `451` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00450` `Mona Hamaki` `20:1097850042` - 20:1097850042 appears in rows 451, 452
- `Critical` `Travelers` row `452` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00451` `Mona's daughter^` `20:1097850042` - 20:1097850042 appears in rows 451, 452
- `Critical` `Travelers` row `157` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00156` `Rasha Bikeer` `20:1099911488` - 20:1099911488 appears in rows 157, 163
- `Critical` `Travelers` row `163` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00162` `Rasha Bikeer` `20:1099911488` - 20:1099911488 appears in rows 157, 163
- `Critical` `Travelers` row `229` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00228` `Rania Ali +` `20:1127311373` - 20:1127311373 appears in rows 229, 230
- `Critical` `Travelers` row `230` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00229` `Rania's Child (5 years)` `20:1127311373` - 20:1127311373 appears in rows 229, 230
- `Critical` `Travelers` row `95` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00094` `Mohammed Riyati` `20:1159280528` - 20:1159280528 appears in rows 95, 108
- `Critical` `Travelers` row `108` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00107` `Eslam island hopping` `20:1159280528` - 20:1159280528 appears in rows 95, 108
- `Critical` `Travelers` row `41` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00040` `Ahmed naim` `20:1277445335` - 20:1277445335 appears in rows 41, 355
- `Critical` `Travelers` row `355` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00354` `Ahmed Naim` `20:1277445335` - 20:1277445335 appears in rows 41, 355
- `Critical` `Travelers` row `331` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00330` `Ahmed Yassin Hassan` `20:1280003592` - 20:1280003592 appears in rows 331, 429
- `Critical` `Travelers` row `429` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00428` `Ahmed Negida` `20:1280003592` - 20:1280003592 appears in rows 331, 429
- `Critical` `Travelers` row `227` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00226` `Mohamed ElSayed Amin` `966:546052012` - 966:546052012 appears in rows 227, 436
- `Critical` `Travelers` row `436` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00435` `Mohamed Rawash` `966:546052012` - 966:546052012 appears in rows 227, 436
- `Critical` `Travelers` row `74` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00073` `Abdulaziz Alnawfal` `966:554421909` - 966:554421909 appears in rows 74, 349
- `Critical` `Travelers` row `349` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00348` `Abdel Aziz Abn Mohamed` `966:554421909` - 966:554421909 appears in rows 74, 349
- `Critical` `Travelers` row `132` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00131` `Mostafa Bassel` `971:563730780` - 971:563730780 appears in rows 132, 176, 177
- `Critical` `Travelers` row `176` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00175` `Mustafa basil` `971:563730780` - 971:563730780 appears in rows 132, 176, 177
- `Critical` `Travelers` row `177` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00176` `Saria` `971:563730780` - 971:563730780 appears in rows 132, 176, 177
- `Critical` `Travelers` row `111` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00110` `Samir hashem` `974:55171371` - 974:55171371 appears in rows 111, 131
- `Critical` `Travelers` row `131` `DUPLICATE_PHONE_LOOKUP_KEY`: `TR00130` `Samir hashem` `974:55171371` - 974:55171371 appears in rows 111, 131
- `High` `Travelers` row `7` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00006` `Reine Abi Rached` `` - Missing: phone
- `High` `Travelers` row `8` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00007` `Linda` `` - Missing: phone
- `High` `Travelers` row `21` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00020` `Ali` `` - Missing: phone
- `High` `Travelers` row `22` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00021` `Julie` `` - Missing: phone
- `High` `Travelers` row `23` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00022` `Timur` `` - Missing: phone
- `High` `Travelers` row `32` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00031` `Mishari Abdulkarim Alawadhi` `` - Missing: phone
- `High` `Travelers` row `44` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00043` `Sara` `` - Missing: phone
- `High` `Travelers` row `46` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00045` `Fatine` `` - Missing: phone
- `High` `Travelers` row `47` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00046` `Basma` `` - Missing: phone
- `High` `Travelers` row `48` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00047` `Nourhan` `` - Missing: phone
- `High` `Travelers` row `49` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00048` `Anna` `` - Missing: phone
- `High` `Travelers` row `51` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00050` `Hadil` `` - Missing: phone
- `High` `Travelers` row `52` `TRAVELER_ID_WITH_MISSING_IDENTITY_DATA`: `TR00051` `Naomi` `` - Missing: phone
