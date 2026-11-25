# Arcanum-authored skills

These five skills live in `skills/arcanum/` and are attributed as
"Arcanum-authored skill" in the registry. They are separate from the pinned
upstream bundle in `skills/vendor/claude-bughunter/`.

| Skill | Purpose |
| --- | --- |
| `api-fingerprint-caido` | Discover API-looking hosts and sibling subdomains in Caido traffic, then fingerprint API candidates with HTTP response/header heuristics. |
| `interceptor` | Drive a signed-in Chrome/Brave browser session and native apps from the command line, including page reads, clicks, typing, network observation, and screenshots. |
| `js_analysis` | Extract API endpoints, paths, and URLs from JavaScript files for recon and bug hunting. |
| `lazy_chunk_downloader` | Locate lazy-loaded JS/CSS chunk references in local bundles and download those chunks. |
| `say_hello` | Small demonstration skill (creates a silly name); useful as a minimal skill-loading example. |

Registry entries for these skills currently carry `license: PENDING`; see
[registry.md](registry.md) and the repository license files in `licenses/`
rather than assuming the vendored upstream license applies to them.

Adding or changing a custom skill requires regenerating the registry:

```sh
python scripts/generate_skill_registry.py
python scripts/validate_skill_registry.py
```
