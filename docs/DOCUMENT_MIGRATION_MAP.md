# Document Migration Map

Non-destructive mapping rules. Component READMEs stay colocated; historical evidence is archived, not deleted.

| Current source group | New path / action | Reason |
|---|---|---|
| root governance docs (`README`, `SECURITY`, `CONTRIBUTING`, `CHANGELOG`, `LICENSE`) | keep root | repository entrypoint/governance. |
| root run/readiness/cleanup reports | `docs/operations/` or `docs/archive/reports/` after review | separate current instructions from historical claims. |
| root `need review.md`, `report1.md`, quarantine outputs | `docs/archive/manual-review/` | generated/manual artifacts. |
| numbered `docs/*.md` phase documents | `docs/archive/phases/` | preserve chronology. |
| `docs/00_status` through `docs/09_archive` | architecture/business/ai-agent/testing/reports/archive by subject | current structure mixes chronology and purpose. |
| `docs/plans/*.md` | keep under `docs/plans/` | active planning. |
| flat setup/architecture/integration docs | operations/architecture/integrations | reduce duplication. |
| `archive/**/*.md` | keep archive | historical evidence. |
| TestSprite/editor-history Markdown | archive/generated or tool-owned | generated snapshots. |
| app/service/package/database/test/script READMEs | keep colocated and index | local ownership. |
| agent prompt Markdown | keep beside runtime prompt | loaded implementation content. |

All 161 discovered Markdown files fit one source group above. Generate a file-by-file link update sheet immediately before any approved move.
