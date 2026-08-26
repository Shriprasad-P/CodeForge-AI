# Dependency advisory review

Stage 6 reviewed the web production dependency graph with `npm audit --omit=dev`
on 2026-08-27. This intentionally excludes test/build-only packages so the
findings reflect runtime reachability; no `npm audit fix --force` or broad
upgrade was run.

The audit reports three high-severity reachable nodes with no automatic fix:

| Package | Reachability | Advisory | Decision |
| --- | --- | --- | --- |
| `next@15.5.22` | Direct production dependency; serves the Next.js application | [npm audit report](https://github.com/advisories/GHSA-qx2v-qp2m-jg93) and related PostCSS/sharp advisories | Track as P1; upgrade only to a release that contains the fixes and re-run the application build/E2E checks. |
| `postcss` (Next transitive) | Used by Next's CSS processing; primarily build/request processing rather than an AgentDock API path | [GHSA-6g55-p6wh-862q](https://github.com/advisories/GHSA-6g55-p6wh-862q), [GHSA-fxqj-rqcc-2cmp](https://github.com/advisories/GHSA-fxqj-rqcc-2cmp), [GHSA-r28c-9q8g-f849](https://github.com/advisories/GHSA-r28c-9q8g-f849) | No lockfile-only fix is available. Keep CSS inputs repository-controlled, do not process attacker-supplied source maps, and monitor Next releases. |
| `sharp` (Next transitive) | Runtime-reachable through Next image optimization if that route is enabled | [GHSA-f88m-g3jw-g9cj](https://github.com/advisories/GHSA-f88m-g3jw-g9cj) | No automatic fix is available. Avoid untrusted remote image sources and upgrade Next/sharp as a tested pair when a compatible release is available. |

The API and worker dependency manifests were not changed by this audit. These
findings are recorded rather than “fixed” blindly because forcing an upgrade
could change the Next runtime and invalidate the deterministic workflow.
