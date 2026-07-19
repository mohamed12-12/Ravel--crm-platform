# File Cleanup Audit

No file was deleted or moved.

| Candidate | Classification | Recommended action |
|---|---|---|
| `__pycache__/`, `.pytest_cache/`, `node_modules/` | generated | safe to regenerate/delete locally. |
| `.tmp-test-*`, `.tmp-test-workdirs/`, `.tmp-debug-gemini/` | test/debug artifact | safe after preserving active investigation evidence. |
| `.history/` | editor history | archive outside tracked repo or ignore; review first. |
| `archive/manual-review/` | historical | keep; exclude from tests. |
| DB backups and workbooks | operational data | requires review; do not delete. |
| migration quarantine outputs/root reports | historical/generated | archive only after owner approval. |

Finding CLEAN-01 | High | Confirmed | persistent test artifacts obscure repository data hygiene. Add cleanup fixture/CI policy after review.
