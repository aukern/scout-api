# Build Ledger

Which pipeline component versions (content hashes) built each slice. Versions are
automatic — each component's version IS its file's content hash; nothing is bumped by
hand. Run `auk-upgrade` to diff these against the pipeline's current `COMPONENTS.lock`
and re-apply only what changed.

| Slice | Components recorded | Lock fingerprint |
|------:|--------------------:|------------------|
| 17 | 211 | `9e1604ce` |
| 18 | 211 | `98373e53`  ← changed since build |
| 19 | 211 | `9e1604ce` |

Pipeline current lock fingerprint: `9e1604ce`
(A slice whose fingerprint differs from current has components that changed since it was built.)
