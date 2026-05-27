# Case Study: AI-Powered Multi-Tenant Chatbot SaaS for Educational Institutions

**Project:** School Chatbot Admin Dashboard
**Developer:** Ruba Ahmed Khan
**Tech Stack:** Python, Flask, OpenAI GPT-4o-mini, Playwright, BeautifulSoup
**Type:** SaaS (Software as a Service)

---

## Executive Summary

A solo-developed, production-ready SaaS platform that enables any school or company to have a fully functional AI chatbot — powered by their own website data — without writing a single line of code. The client adds a one-line `<script>` tag to their website and the chatbot is live within minutes.

---

## Problem Statement

Schools and small businesses in Pakistan frequently struggle to handle repetitive parent/student inquiries — fee structures, admission dates, timings, faculty details. Hiring staff for this is expensive. Off-the-shelf chatbot solutions are either too generic, too expensive, or require extensive manual setup.

**The gap:** No affordable, plug-and-play AI chatbot existed that could read a school's own website and answer questions from it automatically.

---

## Solution

A multi-tenant platform where each client (school/company) is onboarded in minutes:

1. Admin adds the school's website URL to the dashboard
2. The scraper automatically crawls the entire website (up to 50 pages)
3. The scraped data is fed as context to GPT-4o-mini
4. The client embeds one `<script>` tag — chatbot appears on their site instantly

No manual data entry. No chatbot training. No technical knowledge required from the client.

---

## Technical Architecture

### Multi-Tenancy
Each school is completely isolated — separate data directory, separate API key, separate widget. School A's data never leaks to School B.

### Intelligent Scraper
- **Dual-mode scraping:** BeautifulSoup for standard sites, Playwright/Chromium for JavaScript-heavy sites (React, Angular, Next.js)
- **Change detection:** MD5 hash of homepage — scraper skips unchanged websites, saving compute costs
- **Auto page detection:** Automatically identifies fee, admission, contact, results pages by URL keywords
- **50-page limit** with priority ordering

### Smart Context Building
Pages are ranked by importance before being sent to GPT:
- Priority pages (about, fees, admission, contact, founder) → 4000 characters each
- Other pages → 1000 characters each

This ensures GPT finds critical information even in large datasets.

### Response Caching
Identical questions return cached answers instantly — no OpenAI API call. Cache holds up to 500 questions per school, reducing API costs by an estimated 40–60% for high-traffic clients.

### Auto-Sync
APScheduler runs a daily scrape at 6 AM. If the website hasn't changed (hash match), scraping is skipped entirely. If changed, data updates automatically and cache clears.

### Embeddable Widget
A dynamically served JavaScript widget with:
- Conversation memory (last 10 turns sent to GPT for context)
- Page link buttons (e.g., "fees" keyword → opens fee page)
- SVG AI avatar + tooltip
- Multilingual support (English, Roman Urdu, Urdu)
- Zero dependency — works on any website regardless of framework

---

## Key Challenges & Solutions

| Challenge | Solution |
|---|---|
| JS-rendered websites (React/Next.js) couldn't be scraped | Integrated Playwright headless browser with auto-detection |
| GPT missing specific facts in large datasets | Priority-based context ordering, important pages shown first with more characters |
| Scrape blocking HTTP request, causing browser timeout | Background threading — request returns immediately, status polled every 3 seconds |
| Browser caching old widget JS | Cache-Control: no-store headers on widget JS endpoint |
| Admin phone number leaking into chat responses | Removed phone from GPT context entirely; system prompt uses only scraped website data |
| Daily scraping wasting compute on unchanged sites | Homepage MD5 hash comparison — scrape skipped if site unchanged |

---

## Business Model

| Plan | Price | Features |
|---|---|---|
| Starter | Rs. 2,000/month | 1 chatbot, up to 20 pages |
| Growth | Rs. 5,000/month | 3 chatbots, up to 50 pages, priority support |
| Enterprise | Custom | Unlimited, custom branding, dedicated support |

**Client acquisition:** Target school admins, coaching centers, small businesses with websites. Demo takes 10 minutes — show them their own website's chatbot live.

---

## Results (Demo Clients)

- **The City School** — 33 pages scraped, chatbot answers admission, fee, branch queries accurately
- **Yveloxy** — 27 pages scraped, founder story, services, blog content all accessible via chat
- **Young Scholars** — Full school information including timings, faculty, and contact details

---

## Deployment

- **Platform:** Render.com (free tier) / Railway.app
- **Repository:** GitHub (`chatbot-admin-dashboard`)
- **Process Manager:** Gunicorn (1 worker, 120s timeout)
- **Environment:** nixpacks.toml for Chromium/Playwright on cloud

---

## Conclusion

This project demonstrates how a single developer can build a commercially viable SaaS product by combining web scraping, LLM APIs, and a clean multi-tenant architecture. The platform solves a real, underserved problem in the Pakistani education market and is ready for immediate commercial deployment.
