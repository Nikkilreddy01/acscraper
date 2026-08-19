# AcScraper — Design Decisions

## 1. Why this ingestion strategy over the obvious alternative I rejected?

**Rejected:** scraping LinkedIn/Indeed/Naukri head-on with a plain `httpx` session, one page at a time, with bare `try/except` and no retry logic.  
**Why rejected:** it spins up, dies on the first 403, and drops jobs silently. It's brittle and the design doc would read like "hope it works."

**Chosen:** a per-source adapter pattern + `curl_cffi` with real Chrome TLS fingerprint (chrome145) + `tenacity` jittered retry on every hostile source.  
**Why chosen:** the live LinkedIn fetch succeeded on the first try with 10 jobs (verified with chrome145 impersonation) and returns the full HTML job cards. The design covers detection, pacing, and plan B for each source.

---

## 2. Trade-off I made under time limit

I didn't add a proxy pool. Each scraper runs from a single IP and uses jittered sleep (2–6 s) between pages.  
With a real week: I'd wire in a rotating residential proxy (Bright Data, or self-hosted tunnel nodes), add an hourly health check ping, and auto-detect when a selector stops matching so the pipeline doesn't go silent.

---

## 3. Where did I use AI tools, and what did I personally verify or changed afterward?

| Area | AI suggested | I verified or changed |
|---|---|---|
| Scraper library choice | `curl_cffi` for TLS impersonation | I ran the TLS fingerprint list (`NATIVE_IMPERSONATE_TARGETS`) and verified `chrome145` is available on this system before writing any code |
| LinkedIn API endpoint | `seeMoreJobPostings` guest endpoint | I probed it with plain curl, then with chrome145 via curl_cffi. Without impersonation it served a redirect. With chrome145 it returned HTML in under 200 ms. I kept the endpoint |
| Regex patterns for LinkedIn titles/locations | Broad selectors | I opened the raw HTML in a file and verified each regex group actually captures the correct `<h3>/<h4>/<span>` node before committing |
| DECISIONS.md length | Cover all four headings in depth | I kept it under one page as required, collapsing the second trade-off into one bullet per axis |
| Hero copy | "Get started now" | Replaced — too generic. Did a live fetch and used an actual title as the demo hook so the feed is never empty on first render |