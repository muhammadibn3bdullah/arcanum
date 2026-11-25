---
name: api-fingerprint-caido
description: Use whenever the user asks to fingerprint an API and indicates a target host, domain, or URL, even if they do not know the real API hostname. Discover API-looking hosts and sibling subdomains in Caido traffic for the supplied target, fingerprint every API candidate plus the literal target host, and apply HTTP fingerprinting heuristics such as response headers, banners, header order, default error pages, malformed-request behavior, default files, cookie names, API path conventions, and response error shapes. Also use for requests to profile, recon, identify technologies, map API calls, or infer the technology stack of a target API from Caido traffic.
---

# API Fingerprint Caido

## Workflow

1. Extract the target host or URL from the initial user request. Do not ask follow-up questions. Normalize it to a host, but do not assume it is the API host. Keep the original target in the report. If no target exists in the initial prompt, stop with a concise blocked message.
2. Verify Caido access:
   ```sh
   caido-graphql status
   ```
   If the CLI cannot connect or authenticate, explain that Caido must be running (default `http://127.0.0.1:8080/graphql`) and that a different listener can be supplied with `CAIDO_URL` or the CLI's `--endpoint` option.
   If a request returns `INVALID_TOKEN`, Caido's LevelDB localStorage may contain stale historical tokens. Do not ask for a token interactively; report that the run is blocked until the current token is available via `CAIDO_TOKEN`/`--token-file` or Caido DevTools with `JSON.parse(localStorage.CAIDO_AUTHENTICATION).accessToken`.
3. Query captured traffic for the target and related sibling hosts:
   ```sh
   python3 scripts/collect_caido_api_traffic.py https://target.example --limit 300
   ```
   By default this searches the related domain, so a target like `tesla.com` can discover `api.tesla.com`, `owner-api.tesla.com`, and other `*.tesla.com` traffic in Caido. Use `--host-mode exact-host` only when the user explicitly wants the literal host. Use `--httpql` for a narrower filter when needed.
4. Determine actual API hosts from `api_host_candidates` and `host_fingerprints`. Fingerprint every API candidate host and always include the literal target host, even if it scores low or has no captured traffic. If there are many API candidates, group them by hostname and report each host separately with its score/reasons.
5. If no traffic is found, use `caido-graphql operations --filter request` and `introspect` to adapt to schema changes, then retry with simpler HTTPQL such as a quoted related domain string. If there is still no traffic, finish with a report saying Caido has no relevant captured traffic and that the target must be browsed/proxied through Caido before the skill can fingerprint it.
6. Read `references/http-fingerprinting.md` before analyzing results. Use the "Technique Coverage Matrix" as a mandatory checklist; every report must include a covered/not-covered note for each technique for each fingerprinted host where possible.
7. For techniques requiring active requests, do not execute probes by default. If the initial prompt explicitly authorizes active testing and the target scope is clear, generate the exact probe plan and execute only those low-volume probes without asking another question:
   ```sh
   python3 scripts/plan_http_fingerprint_probes.py https://target.example
   ```
   Keep probes limited to banner GET, one random 404, default file HEAD/GET checks, and two malformed request parser checks. If initial active authorization is absent or ambiguous, do not ask; mark active-only techniques as not covered and include the generated probe plan as recommended follow-up.
8. Do not use browser/search, Wappalyzer, Shodan, WhatWeb, Nmap, or any other external corroboration for this skill. Use only Caido traffic and explicitly mark external corroboration as out of scope.
9. Produce a concise fingerprint report:
   - Target and Caido filter used.
   - API discovery: related domain searched, all hosts observed, API host candidates with score/reasons, and whether the literal target host had captured traffic.
   - API surface found per host: endpoint patterns, methods, status codes, content types.
   - Technique coverage matrix: header analysis, banner/proxy identification, header order, default error pages, malformed request behavior, default files/directories, cookie parameters, and external corroboration marked out of scope.
   - Technology indicators with evidence: header names/values, explicit banners, header ordering clues, cookies, response body phrases, default page signatures, default files/directories, file extensions, protocol hints, and CDN/WAF/proxy clues from Caido traffic only.
   - Confidence per inference: High for explicit version/banner/header values, Medium for repeated cookie/header/body signatures, Low for header order or single weak clues.
   - Recommended next passive/low-impact checks and any authorized active follow-up. Do not perform fuzzing or broad scans.

## Caido Notes

- The CLI lives at `caido-graphql` and defaults to `http://127.0.0.1:8080/graphql`.
- It auto-discovers Caido's Electron localStorage access token, but supports `--token`, `CAIDO_TOKEN`, `CAIDO_ACCESS_TOKEN`, and `--token-file`.
- Caido's GraphQL schema changes across releases. Prefer live introspection and the bundled collector script over hard-coded assumptions.
- HTTPQL filters go in GraphQL variables as `{ "filter": { "code": "req.host.cont:\"example.com\"" } }`.
- The collector emits aggregate fields for each passive technique: `api_host_candidates`, `host_fingerprints`, `technology_header_values`, `header_order_guesses`, `default_error_signatures`, `path_and_file_signals`, `cookie_technologies`, `api_paths`, and raw header/cookie/body samples per record.

## Report Rules

- Never ask follow-up questions during skill execution. Proceed from the initial prompt, use conservative defaults, or finish with a clear blocked/no-data result.
- Separate observed facts from inferred technologies.
- Quote only short evidence snippets; avoid dumping raw requests/responses unless the user asks.
- Redact obvious secrets from headers, cookies, authorization values, API keys, and tokens.
- Keep active probing out of the workflow by default. When active probing is authorized, state the exact probes first and keep them low volume.
- Do not browse the web or use external tools/services for corroboration as part of this skill.
