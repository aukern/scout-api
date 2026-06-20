# Build Ledger

Which pipeline component versions (content hashes) built each slice. Versions are
automatic — each component's version IS its file's content hash; nothing is bumped by
hand. Run `auk-upgrade` to diff these against the pipeline's current `COMPONENTS.lock`
and re-apply only what changed.

| Slice | Components recorded | Lock fingerprint |
|------:|--------------------:|------------------|
| 17 | 211 | `2830a523` |

Pipeline current lock fingerprint: `2830a523`
(A slice whose fingerprint differs from current has components that changed since it was built.)
