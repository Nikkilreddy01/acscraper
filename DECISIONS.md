# AcScraper — Design Decisions & Architecture

## 1. Why this ingestion strategy over the obvious alternative I rejected?

**Rejected:** Scraping job boards head-on with standard `httpx`/`requests` sessions, single-threaded execution, and brittle CSS-only selectors.  
**Why rejected:** Modern job boards (LinkedIn, Indeed, Glassdoor, ZipRecruiter, Naukri) enforce Akamai Bot Manager, Cloudflare Turnstile, and TLS/JA3 fingerprint checks. Standard Python HTTP clients get rejected on TCP/TLS handshakes with immediate 403/406 status codes.

**Chosen:** A dual-engine adapter architecture:
1. **Primary Stealth Engine (`curl_cffi`):** Real Chrome 120/145 JA3 fingerprint impersonation + HTTP/2 ALPN negotiation for sub-second REST/GraphQL/JSON ingestion (RemoteOK, LinkedIn guest APIs, Indeed Mosaic payloads, Glassdoor GraphQL, Hacker News Algolia).
2. **Automated Headless Fallback (`Playwright`):** Headless Chromium singleton pool with automation flags disabled (`--disable-blink-features=AutomationControlled`) to intercept internal XHR network responses on heavy SPA platforms.
3. **Timeline Filtering (`timeframe`):** Standardized time-range parameters (`24h`, `7d`, `1m`, `3m`, `6m`, `1y`) mapped directly to platform-native parameters (`f_TPR`, `fromage`, `numericFilters`, `days`, `epoch`).

---

## 2. Trade-offs made under time limit vs. What I'd do with a real week

- **Trade-off:** Ran on a single host IP with human-like jittered pacing (2–5s) and in-memory LRU caching with stale-while-revalidate.
- **With a real week:**
  - Connect a residential proxy rotator with automated IP health scoring.
  - Implement Kafka/RabbitMQ task queue with worker workers for scheduled hourly incremental scraping.
  - Implement dynamic DOM selector drift detection to auto-alert when job board markup changes.

---

## 3. Where AI tools were used vs. What I verified and changed

| Area | AI Suggested | What I Verified & Built |
|---|---|---|
| **TLS Spoofing** | `curl_cffi` library | Verified native JA3 fingerprinting targets (`chrome120`/`chrome145`) and eliminated manual conflicting headers that triggered Akamai/Cloudflare flags. |
| **Indeed Scraping** | Brittle regex on `jobTitle` | Discovered Indeed's internal `window.mosaic.providerData["mosaic-provider-jobcards"]` JSON payload and built a clean parser extracting titles, salaries, locations, and direct job keys. |
| **Timeline Engine** | Post-filtering only | Implemented native upstream query parameters (`f_TPR` for LinkedIn, `fromage` for Indeed/Glassdoor, `created_at_i` for Hacker News, `epoch` for RemoteOK) to minimize network payload. |
| **UX & Frontend** | Auto-fetch on render | Replaced with explicit on-demand console triggers, interactive terminal HUD logs, live latency metrics, and instant client-side search. |
