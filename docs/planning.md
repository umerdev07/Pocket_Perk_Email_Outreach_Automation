# PocketPerks – Planning Pack (PRD, TRD, App Flow, UI/UX, Schema, Plan)

Oct 31, 2026 · @Umer

## 1. Overview

PocketPerks ek simple email outreach automation tool hai: leads import karo, campaign banao, AI se drafts likhwao, har business ke liye personalize karo, risky emails ko human approval do, Instantly se bhejo aur replies ek jagah dekho. Yeh CRM nahi hai.

**Core pipeline:** Import Leads → Campaign + generic drafts (Claude API) → Draft approval → Start Campaign (per-lead personalization) → Risk assessment → Human Approval (MEDIUM/HIGH) → Instantly send → Lead Replies.

**Scope (in):** CSV/Excel import, campaign creation, generic draft review, personalization, risk routing, Instantly sending, reply inbox, test mode.

**Scope (out):** manual lead entry, dashboards, analytics, RBAC/enterprise security, billing, multi-user teams, production hardening (baad mein).

**Build rules:** naya project alag folder mein, purana project overwrite nahi hoga; fake data ya mock responses ko real integration nahi kaha jayega; secrets sirf backend `.env` mein; frontend aur backend alag.

**Target:** UK ke independent cafés aur restaurants. Email mein PocketPerks Loyalty Stamp ka zikr ho, business ke saath resonate kare (unki category/city ke hawale se) aur unhein convince kare ke repeat customers ke liye yeh kaam ka hai.

## 2. PRD (Product Requirements)

**User:** ek non-technical outreach operator jo bina technical knowledge ke poora workflow chala sake. Local MVP mein single user, login nahi.

**Success:** user 15 steps ka workflow (CSV upload se reply dekhne tak) bina developer ki madad ke complete kar sake.

### 2.1 Import Leads

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| IL-1 | CSV aur .xlsx upload | Dusri file types reject, clear error message |
| IL-2 | Backend poori file process kare | 10,000 rows ki file bhi poori import ho |
| IL-3 | Frontend sirf pehli 20 rows preview kare | Preview mein 20 se zyada rows kabhi nahi |
| IL-4 | Validate + normalize | Email lowercase/trim, website se domain nikalna, invalid email flag. File ke 11 columns map hon: Business name, Business category, Business email, Phone number, Website URL, City, Full address, Email source URL, Email verification result, Verification tool, Quality flags |
| IL-5 | Duplicate check | Email, website aur domain par; DB mein pehle se maujood aur file ke andar dono |
| IL-6 | Import summary | Imported / Duplicate / Invalid / Rejected counts + reason |
| IL-7 | Start Campaign button | Import ke baad prominent; campaign select kiye bina start nahi hota |

### 2.2 Campaigns

| ID | Requirement | Acceptance criteria |
| --- | --- | --- |
| CP-1 | Form: Campaign Name, Total Emails, Start Date, End Date, Instructions | Required fields; Total Emails 1 se 10; end date start date se pehle na ho |
| CP-2 | Dynamic Email N Context fields | Total Emails badalne par context textareas utne hi dikhen |
| CP-3 | Create Draft Campaign | Campaign save + Claude API se har step ka generic draft |
| CP-4 | Draft review | Subject/body edit, regenerate, approve per step |
| CP-5 | Sirf approved drafts use hon | Saare steps approved na hon to campaign "approved" nahi hota |

### 2.3 Start Campaign (Personalization)

- User lead batch aur approved campaign chunta hai.
- Har eligible lead ke liye har email step ka personalized subject + body banta hai.
- AI sirf approved draft aur lead ke asli data (name, type, location, website) use kare; koi offer, achievement ya fact invent na kare.
- Har generated email lead, campaign aur step se linked ho.
- Missing business name wali lead ko "skipped" mark karke reason dikhana.

**Email ka andaz:** PocketPerks Loyalty Stamp ka faida (repeat customers, stamp card ki jagah digital) us business ki category (café/restaurant) aur city se jorh kar likhna; chhoti, friendly, convincing email; ek clear call-to-action; sender ka naam/address email mein rahe. **Eligibility:** jis lead ki `verification_result` valid na ho ya `quality_flags` mein masla ho, usay skip kar ke wajah dikhana (Start Campaign par count dikhe).

### 2.4 Risk Assessment aur Human Approval

| Risk | Matlab | Action |
| --- | --- | --- |
| LOW | Saari content checks pass | Sending queue mein (provider send confirm hone tak `sent` nahi) |
| MEDIUM | Review chahiye | Auto-send nahi, Human Approval queue |
| HIGH | Hold par | Human Approval queue, auto-release kabhi nahi |

- Reviewer subject/body edit, approve ya reject kar sakta hai.
- Approved email edit ho to dobara approval zaroori.
- AI check har personalized email ko campaign instructions, us email-step ke context, approved generic draft aur lead facts ke against compare kare.
- Spam-like wording ek heuristic risk signal hai; AI/email content se actual spam-folder placement ya deliverability guarantee nahi hoti.
- Human approve karne par email `queued` ho (ready for sender); `sent` sirf Instantly ki successful confirmation ke baad.

### 2.5 Sending (Instantly)

- Multi-step campaigns support.
- Real sending se pehle test mode (koi real email nahi jati).
- Sending ka real status track ho; fake success response kabhi nahi.
- User campaign, imported lead batch aur start/end time choose karke ek baar **Start Campaign** kare. Backend ka durable worker personalization, risk routing aur eligible scheduled sends background mein continue kare; browser/dialog close hone se run na ruke.
- Har outgoing marketing email mein PocketPerks/sender ki pehchan aur seedha opt-out tareeqa ho (for example, recipient reply karke future emails rok sake). Opt-out ko normalized email ke saath suppression/do-not-contact record mein save karo, aur har future import/run/send se pehle us list ke against check karo. Opt-out request ke baad us recipient ko dobara send na ho.
- Sending se pehle lead ki subscriber/business type eligibility record ya verify ho. Opt-out line individual subscribers ke liye consent/soft-opt-in ki zaroorat ko replace nahi karti; uncertain eligibility par send rok kar review/confirmation chahiye.

### 2.6 Lead Replies

- Central inbox: Business Name, Email, Campaign, Original Email, Reply, Received Date, Status.
- Reply kholne par poori conversation dikhe.
- Campaign-wise alag inbox nahi.

### 2.7 Non-functional

- Har button apna kaam kare; loading, success, empty aur error states har page par.
- API keys frontend mein kabhi nahi, repo mein commit nahi.
- Integrations modular hon taake alag alag test ho saken.

## 3. TRD (Technical Requirements)

Frontend sirf FastAPI se baat karta hai; FastAPI Supabase, Claude API aur Instantly se. Koi secret browser tak nahi pahunchta.

### 3.1 Stack

| Layer | Technology | Zimmedari |
| --- | --- | --- |
| Frontend | Next.js + TypeScript (App Router), Tailwind | 4 pages, forms, tables |
| Backend | FastAPI + Python 3.11+, Pydantic | Business logic, APIs, background jobs |
| Database | Supabase PostgreSQL | Saara data; backend service key se access |
| AI | Claude Messages API | Generic drafts, personalization, risk assessment |
| Email | Instantly API v2 | Sending, status, replies |
| File parsing | pandas + openpyxl | CSV/.xlsx processing |

### 3.2 Folder structure

```
pocketperks/
  backend/
    app/
      main.py, config.py, db.py
      routers/    leads.py campaigns.py drafts.py personalization.py approvals.py sending.py replies.py
      services/   import_service.py draft_service.py personalize_service.py risk_service.py send_service.py reply_service.py
      integrations/ claude_client.py instantly_client.py
      schemas/    (Pydantic models)
    tests/
    requirements.txt, .env.example
  frontend/
    app/ (import-leads, campaigns, human-approval, lead-replies)
    components/, lib/api.ts
  supabase/migrations/
  docs/
```

### 3.3 Backend modules

- **Import service:** file read, normalize, duplicate check, bulk insert. Poori file backend process karta hai; response mein sirf pehli 20 rows ka preview.
- **Claude client:** ek module, model name `.env` se (`CLAUDE_MODEL`), JSON output handling, timeout aur retry (max 2). API key sirf backend secret store mein.
- **Personalization:** background task; lead-by-lead loop, har email ka status DB mein; ek lead fail ho to baqi chalte rahen.
- **Risk service:** rule-based checks + AI check (neeche).
- **Instantly client:** alag module, test mode ke saath.
- **Reply service:** webhook endpoint + polling fallback.

### 3.4 Risk engine

Pehle rule checks, phir AI check. Final level = dono mein se zyada severe.

| Check | Type | Level agar fail |
| --- | --- | --- |
| Subject/body khali ya placeholder bacha (`{{name}}`, `[Business]`) | Rule | HIGH |
| Spam words (free money, guaranteed, act now, ALL CAPS, bohot `!`) | Rule | MEDIUM |
| Body mein link ya number jo draft ya lead data mein nahi tha | Rule | MEDIUM |
| Subject length > 80 ya body > 200 words | Rule | MEDIUM |
| Lead data mein business name/email missing | Rule | HIGH |
| Personalized email campaign instructions ya us step ke context ko miss/contradict kare | AI (JSON: level + specific reason) | MEDIUM/HIGH |
| AI: unsupported claim, invented fact, irrelevant ya offensive content | AI (JSON: level + specific reason) | AI jo level de |
| AI: obvious spam-like wording | AI (JSON: level + specific reason) | MEDIUM/HIGH; actual inbox placement ka dawa nahi |

Har email ke saath `risk_level` aur `risk_reason` save hoga; reviewer ko reason dikhta hai.

### 3.5 Instantly sending (pehle docs verify karna)

Instantly ko actual email delivery, provider status aur replies ke liye use karna; lead discovery/Apify integration scope mein nahi. Apify research user manually karega aur CSV/XLSX PocketPerks mein import karega. User ne confirm kiya hai ke campaign start par eligible leads Instantly mein bhi campaign ke saath sync ho sakti hain; Supabase PocketPerks ka record of truth rahega.

Official API v2 docs review (Stage 7.2, 2026-11-05):
- `POST /api/v2/campaigns` campaign ke liye sequence, sending accounts (`email_list`), schedule, start/end dates aur daily limit accept karta hai. Sequence step mein subject/body variants aur per-step `delay`/`delay_unit` fields hain. Campaign schedule mein `start_date`/`end_date`, daily `timing.from`/`timing.to`, active days aur timezone hain.
- `POST /api/v2/leads/add` ek request mein 1–1000 leads ko `campaign_id` ke saath add karta hai; email required hai, custom variables supported hain, aur duplicate/invalid checks ka response deta hai. Bulk sync ko 1000 ya us se chhote chunks mein bhejo. PocketPerks mein each created lead ka Instantly ID aur campaign/run mapping persist karo; retry idempotent ho.
- Har email step ke liye campaign template mein unique custom variables (for example, `pp_step_1_subject`, `pp_step_1_body`) use kiye ja sakte hain; lead ke `custom_variables` mein us lead ke personalized subject/body bheje jayen. Instantly help docs confirm karti hain ke custom variables subject aur body dono mein resolve hote hain. Is se per-lead, per-step content Instantly sequence ke zariye send ho sakta hai.
- User clarification (2026-11-05): follow-up steps ke darmiyan **kuch ghanton** ka gap chahiye, 3 din ka nahi. Exact default hours implementation se pehle confirm karna hai. Har lead ke step gap ko configured hours mein enforce karo; agar end-window mein steps safely fit na hon to run hold karke reason dikhaye.
- Follow-up gap aur overall sending pace alag controls hain: sequence delay ek lead ke Email 1→2→3 ko space karta hai; Instantly ka `email_gap`/sending-account behavior alag recipients ke sends ko pace karta hai. Burst sending rokne ke liye shuru mein ek sender account use karo; multiple accounts par scale karne se pehle sequential/low-rate behavior verify karo. Yeh pacing spam placement ko guarantee se nahi rok sakti; Inbox placement domain authentication, sender reputation, recipient engagement aur provider filters par bhi depend karti hai.
- API schedule daily sending window aur timezone support karta hai, magar `start_date`/`end_date` date-only fields hain. Exact start/end datetime ko PocketPerks durable scheduler enforce karega: start se pehle activation/send nahi, end datetime par pause/stop. Instantly campaign schedule ko additional provider-side guard ke taur par use karo.
- `email_sent`, `reply_received`, `email_bounced`, `lead_unsubscribed` webhook event types available hain. Webhook primary status/reply path hoga; `GET /api/v2/emails` polling fallback ke liye `emails:read` scope lega. Webhook idempotency aur signature/secret validation PocketPerks mein implement honi hai.
- API v2 test endpoint `POST /api/v2/emails/test` `emails:create` maangta hai aur turant real test email bhejta hai; yeh dry-run nahi aur campaign sequence test ka substitute nahi. PocketPerks `SEND_MODE=test` ko no-send behavior hi rehna hai.

Required least-privilege scopes for planned API path: existing `accounts:read` and `campaigns:read`, plus `campaigns:create`, `campaigns:update`, `leads:create`, `webhooks:create` (and `webhooks:read` if checking existing hook config), and `emails:read` for polling fallback. Existing API key scopes cannot be expanded; create a separate v2 key. `emails:create` only if we explicitly add the separate immediate test-email feature. Do not use `all:all`.

Sending account policy: allow only the five active `@apppocketperks.com` accounts selected for this PocketPerks workspace; explicitly exclude `praveen@takeawaymax.co.uk`. Start controlled verification with one PocketPerks sender account. Keep warmup running; account Active status alone is not deliverability proof.

No generic new-outbound `/send` API is documented. The supported design is create/use an Instantly campaign with a sequence, add the batch leads to it, and activate it only after the durable PocketPerks scheduler/risk gates permit. Instantly campaign and lead records will coexist with PocketPerks records by user decision.

**Client ka Instantly account:** API key ka read-only test ho chuka hai aur 6 connected sending accounts list hue. Yeh sirf account-read verification tha; is se sending capability ya email delivery verify nahi hoti.

- `SEND_MODE=test` (default): real activation/sending band rehte hain. Current integration phir bhi Instantly par campaign draft aur leads sync kar sakti hai; ise fully offline/dry-run na samjhein. Provider API ke baghair load rehearsal ke liye mocked providers aur isolated data use hon.
- `SEND_MODE=live`: real Instantly API writes, activation aur email delivery mumkin hain; actual provider response save hota hai. Live gate sirf controlled, approved testing ke liye on karein.

### 3.6 Replies

- Primary: Instantly webhook (reply event) → `POST /api/webhooks/instantly`.
- Fallback: manual "Refresh" button jo Instantly se replies fetch kare.
- Local mein webhook ke liye tunnel (ngrok/cloudflared) chahiye; match lead email + Instantly campaign/lead id se.
- Opt-out/rejection replies ko record karke suppression list mein daalne ka workflow define karo; ambiguous natural-language replies ko automatic opt-out samajhne ke bajaye human review mein bhejo.

### 3.7 Environment variables (backend `.env` only)

`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `CLAUDE_API_KEY`, `CLAUDE_MODEL`, `INSTANTLY_API_KEY`, `SEND_MODE`, `FRONTEND_ORIGIN`. Frontend mein sirf `NEXT_PUBLIC_API_URL`.

**Claude model:** `CLAUDE_MODEL` se model configure hota hai. Claude Messages API ko use karne ke liye client-provided, rotated API key aur approved model/budget chahiye; key sirf backend secret store mein hogi, chat, frontend ya repository mein nahi. Rate-limit retries ke bawajood incomplete lead-by-lead work resumeable rehna chahiye.

### 3.8 API endpoints (summary)

| Area | Endpoints |
| --- | --- |
| Leads | `POST /api/leads/import`, `GET /api/lead-batches`, `GET /api/lead-batches/{id}` |
| Campaigns | `POST /api/campaigns`, `GET /api/campaigns`, `GET /api/campaigns/{id}` |
| Drafts | `POST /api/campaigns/{id}/drafts/generate`, `PATCH /api/drafts/{id}`, `POST /api/drafts/{id}/regenerate`, `POST /api/drafts/{id}/approve` |
| Start | `POST /api/campaigns/{id}/start` (body: batch\_id), `GET /api/runs/{id}` |
| Approvals | `GET /api/approvals`, `PATCH /api/emails/{id}`, `POST /api/emails/{id}/approve`, `POST /api/emails/{id}/reject` |
| Sending | `POST /api/sending/process` (LOW + approved emails bhejta hai), `GET /api/emails/{id}/status` |
| Replies | `GET /api/replies`, `GET /api/replies/{id}`, `POST /api/replies/refresh`, `POST /api/webhooks/instantly` |
| System | `GET /api/health` |

### 3.9 Error handling aur testing

- Har API consistent JSON error `{error, detail}` de; frontend ise toast/inline message mein dikhaye.
- Claude API/Instantly timeouts par retry, phir clear failure state.
- Tests: pytest (import, duplicate, risk rules, state transitions, test-mode sending), integrations ke liye alag tests jinme real calls opt-in hon; frontend ke liye basic component/page tests.

## 4. App Flow

&#91;embedded content: PocketPerks end-to-end flow · 5 setup steps, 1 risk decision\]

Steps 1 se 5 user karta hai; uske baad risk check tay karta hai ke email seedha send queue mein jaye (LOW) ya Human Approval se guzre (MEDIUM/HIGH). Reject hui email kabhi nahi bheji jati, aur approve ke baad edit hone par dobara review hoti hai.

## 5. UI/UX Design Brief

**Look:** saaf, professional, light theme; ek accent colour (indigo/blue), neutral greys, 8px spacing grid, ek font (Inter), rounded 8px cards. Technical jargon nahi: "Lead batch", "Email 1", "Needs review" jaise labels.

**Layout:** left par fixed sidebar (240px) sirf 4 items; right par main content (max-width 1200px). Mobile par sidebar hamburger drawer ban jata hai.

**Sidebar:** Import Leads · Campaigns · Human Approval (pending count badge) · Lead Replies (unread count badge). Neeche chhota "TEST MODE" indicator jab `SEND_MODE=test` ho.

### 5.1 Import Leads

- Upload card: drag-and-drop ya file choose, sirf .csv/.xlsx, "Upload" button.
- Upload ke baad automatic preview table: pehli 20 rows, upar "Showing first 20 of N rows".
- Summary cards: Imported, Duplicates, Invalid, Rejected; har ka reason list expand ho sake.
- Prominent **Start Campaign** button. Click par modal: approved campaign dropdown (sirf approved), "Start" confirm. Koi approved campaign na ho to message + Campaigns page ka link.
- Neeche purane lead batches ki chhoti list (name, date, counts).

### 5.2 Campaigns

- Campaign list (name, schedule date range, emails, status badge: Draft / Approved) + "New Campaign" button.
- Form: Campaign Name, Total Emails (number), Start Date, End Date, Instructions; Total Emails se Email 1…N Context textareas dynamically banen. Button: **Create Draft Campaign** (generation ke dauran spinner, button disabled).
- Draft review: har step ka card (Subject input, Body textarea, Regenerate, Approve). Step approve hone par green "Approved" tag; edit karne par wapas "Edited – approve again". Page ke top par "3 of 3 approved" progress.

### 5.3 Human Approval

- Queue table/cards: Business, Campaign, Step, Risk badge (MEDIUM amber, HIGH red), Reason, Subject.
- Row click par detail panel: editable subject aur body, **Approve** aur **Reject** buttons. Edit karne par "Save & Approve".
- Filter: All / Medium / High. Empty state: "Koi email review ke liye nahi hai".

### 5.4 Lead Replies

- Inbox style: left par reply list (business, email, snippet, date, status dot), right par selected reply ka conversation thread (original email upar, reply neeche).
- Status: New / Read. "Refresh" button. Empty state: "Abhi koi reply nahi aya".

### 5.5 Har page par zaroori states

| State | Behavior |
| --- | --- |
| Loading | Skeleton ya spinner; submit buttons disabled |
| Success | Toast + UI refresh |
| Empty | Friendly message + agla step ka button |
| Error | Inline red message jo asli wajah bataye, retry option |

### 5.6 Accessibility

Keyboard navigable forms, visible focus, labels har input ke saath, risk sirf colour se nahi balki text badge se bhi dikhe.

## 6. Backend Schema (Supabase PostgreSQL)

8 tables workflow ke mutabiq: `lead_batches`, `leads`, `campaigns`, `campaign_steps`, `generic_drafts`, `personalized_emails`, `campaign_runs`, `replies`. Sab IDs `uuid` (default `gen_random_uuid()`), sab tables mein `created_at timestamptz default now()`.

### 6.1 Relationships

`lead_batches` 1→N `leads` · `campaigns` 1→N `campaign_steps` 1→1 `generic_drafts` · `campaign_runs` link karta hai (batch, campaign) · `personalized_emails` link karta hai (lead, campaign, step, run) · `replies` N→1 `personalized_emails`/`leads`/`campaigns`.

### 6.2 Tables

**lead\_batches**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| file\_name | text |  |
| total\_rows | int | poori file ki rows |
| imported\_count, duplicate\_count, invalid\_count, rejected\_count | int | summary |
| created\_at | timestamptz |  |

**leads**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| batch\_id | uuid FK → lead\_batches |  |
| business\_name | text | required |
| email | text | lowercase, unique |
| website | text | normalized |
| domain | text | website/email se nikala hua, unique (non-null) |
| business\_category | text |  |
| start\_date | date | Campaign sending window begins |
| end\_date | date | Campaign sending window ends |
| extra | jsonb | file ke baqi columns |
| created\_at | timestamptz |  |

Unique indexes: `email`, `domain`. Rejected, duplicate aur invalid rows `leads` mein nahi jatin; unki details `lead_batches.reject_log` (jsonb, lead\_batches ka extra column) mein save hoti hain.

**Lead file ke columns (client ki file):** Business name → `business_name`, Business category → `business_category`, Business email → `email`, Phone number → `phone`, Website URL → `website`, City → `city` (upar table ke `location` ki jagah), Full address → `full_address`, Email source URL → `email_source_url`, Email verification result → `verification_result`, Verification tool → `verification_tool`, Quality flags → `quality_flags`. Yeh 7 naye/renamed columns `leads` table mein add hon; `extra` jsonb sirf unknown columns ke liye.

**campaigns**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| name | text |  |
| total\_emails | int | 1 se 10 |
| location | text |  |
| instructions | text |  |
| status | text | `draft` / `approved` |
| created\_at | timestamptz |  |

**campaign\_steps**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| campaign\_id | uuid FK |  |
| step\_number | int | unique (campaign\_id, step\_number) |
| context | text | user ka Email N Context |
| delay\_value | numeric | step 1 = 0; follow-up delay configured value |
| delay\_unit | text | `hours` / `days` / `minutes`; sequence follows each saved campaign step's `delay_days` |

**generic\_drafts**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| step\_id | uuid FK, unique | ek step = ek draft |
| subject | text |  |
| body | text |  |
| status | text | `generated` / `edited` / `approved` |
| approved\_at | timestamptz |  |
| updated\_at | timestamptz |  |

Campaign tab `approved` hota hai jab uske saare steps ke drafts `approved` hon.

**campaign\_runs**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| campaign\_id | uuid FK |  |
| batch\_id | uuid FK |  |
| status | text | `running` / `completed` / `failed` |
| total\_leads, processed, skipped, failed | int | progress |
| created\_at, finished\_at | timestamptz |  |

**personalized\_emails**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| run\_id | uuid FK |  |
| lead\_id | uuid FK |  |
| campaign\_id | uuid FK |  |
| step\_id | uuid FK | unique (run\_id, lead\_id, step\_id) |
| subject, body | text | current version |
| risk\_level | text | `LOW` / `MEDIUM` / `HIGH` |
| risk\_reason | text |  |
| status | text | neeche state list |
| approved\_version\_hash | text | approve hue subject+body ka hash |
| reviewed\_at | timestamptz |  |
| send\_mode | text | `test` / `live` |
| instantly\_ref | text | Instantly se mila id |
| send\_error | text |  |
| sent\_at | timestamptz |  |

**replies**

| Column | Type | Note |
| --- | --- | --- |
| id | uuid PK |  |
| email\_id | uuid FK, nullable | match na ho to null |
| lead\_id | uuid FK, nullable |  |
| campaign\_id | uuid FK, nullable |  |
| from\_email | text |  |
| subject | text |  |
| body | text |  |
| instantly\_id | text unique | duplicate webhook se bachne ke liye |
| received\_at | timestamptz |  |
| status | text | `new` / `read` |

### 6.3 Email status machine

`generated` → (LOW) `queued` / (MEDIUM, HIGH) `pending_review` → (human approves) `queued` / (human rejects) `rejected` → `sent` ya `sent_test` ya `failed`.

Approval ke baad subject/body edit ho to hash badalne se status wapas `pending_review`.

| Status | Matlab |
| --- | --- |
| generated | AI ne banaya, risk check baqi |
| pending\_review | Human Approval queue mein |
| approved | Legacy/intermediate approval state; new approvals `queued` mein jati hain |
| rejected | Reviewer ne reject kiya, kabhi nahi bhejna |
| queued | Rule checks pass hue ya reviewer ne approve kiya; bhejne ke liye tayyar, magar abhi sent nahi |
| sent / sent\_test / failed | Final sending result |

### 6.4 Indexes aur rules

- Index: `leads(batch_id)`, `personalized_emails(status)`, `personalized_emails(risk_level)`, `replies(received_at desc)`.
- MEDIUM/HIGH risk email ka `queued` hona sirf explicit human approval ke baad mumkin; backend conditional status transition aur review metadata enforce kare (sirf UI par nahi).
- Local MVP mein RLS band, backend service key use karta hai; key kabhi frontend mein nahi.

## 7. Implementation Plan

9 stages, har stage ke baad: kya bana, kaun se tests pass hue, kya baqi hai. Aglay stage par tabhi jao jab current stage ka exit check pass ho.

**Stage 4.1 (complete, 2026-11-06):** OpenRouter ko Claude Messages API se replace kiya. Draft generation, per-lead personalization aur risk assessment ek hi backend Claude client use karte hain; `CLAUDE_API_KEY` aur `CLAUDE_MODEL` backend-only settings hain. Ek minimal live call se configured Sonnet model ka response confirm hua; unit suite nahi chalayi gayi. Approval, Instantly sync/launch, replies ya scheduling behavior is provider change mein nahi badla.

**Stage 4.2 (complete, 2026-11-06):** Harrogate kit ke approved product facts aur sandwich-shop, café aur barber messaging shared backend prompt guidance mein apply ki gayi. Existing campaign instructions, step context, verified lead category, human review, opt-out, Instantly sync/launch aur schedule authoritative rahe. Later copy-guard follow-up Stage 4.3 mein complete hua.

| Stage | Kaam | Deliverable | Exit check |
| --- | --- | --- | --- |
| 1. Foundation | Naya folder, FastAPI + Next.js setup, Supabase connection, initial migration (8 tables), `/api/health`, CORS, `.env.example` | Dono apps local chalte hain, frontend health call dikhata hai | `pytest` health test pass; frontend se backend + DB connection confirm |
| 2. Import Leads | Upload endpoint, CSV/.xlsx parse, normalize, email/domain duplicate check, summary, preview (20 rows), Import Leads page | Poori file DB mein, preview 20 rows | Tests: duplicates, invalid email, 100+ rows file mein preview sirf 20, empty/galat file error |
| 3. Campaign Management | Campaign name, instructions, total emails, per-step contexts, campaign start/end dates; campaign + steps save/list; delete a step while campaign remains draft | Campaign banta, date range save hoti aur list mein dikhti hai | Date order and required-field validation; deletion preserves at least one step and re-approval |
| 4. AI Drafts | Claude client, generic draft generation, draft edit/regenerate/approve, campaign status `approved` | Har step ka approved draft DB mein | Claude call (rotated key ke saath) se draft; edit ke baad re-approve; unapproved campaign start nahi hota |
| 5. Personalization | Start Campaign endpoint, durable PGMQ run queue, restart-resumable worker, bounded page/chunk processing, per-lead per-step personalization, run progress and per-step failure details | `personalized_emails` rows har lead x step ke liye; saved lead/step pairs retry par reuse; missing steps ki safe reason run report mein | Apply queue migration; test batch par sahi lead/campaign/step link; restart/retry par duplicate email rows ya unnecessary repeat AI calls nahi; ek lead fail hone par run nahi rukta; failures explainable; koi invented fact nahi (sample review) |
| 6. Risk + Human Approval | Lightweight content-level spam-risk checks, LOW/MEDIUM/HIGH routing, approval/review history, edit/approve/reject, risk recheck | Only obvious content/format spam signals need human review; LOW goes to queued (not sent) | Risk checks do not claim Gmail inbox placement; history records automated checks and human decisions |
| 7. Instantly + durable scheduling | Official API verified; durable lead sync, approval/suppression gates, automatic activation in explicitly enabled live mode, campaign-local timezone/date/time schedule, follow-up delays, provider status and reply tracking | Eligible leads sync; Instantly campaign activates at the PocketPerks start timestamp and pauses at its end timestamp | No duplicate/ambiguous writes; test mode cannot activate; controlled live pilot and exact end-time pause verified |
| 8. Lead Replies | Polling-based sync, reply matching, filters, suppression, admin audit/release/delete, inbox UI | Replies PocketPerks mein dikhte hain aur opt-outs suppress hote hain | Webhook security/idempotency, unmatched-reply handling aur full thread view verify/finish |
| 9. Final feature validation | 200-lead isolated mock rehearsal, recovery/duplicate checks, UI/error-state review, backend + frontend checks | Feature-complete sign-off | 200 synthetic leads live providers ko touch kiye baghair pass; controlled pilot results reviewed; all checks green |

### 7.1 Har stage ke baad report

Format: **Bana kya** · **Tests (pass/fail)** · **Baqi kya** · **Koi decision jo user se chahiye**.

### 7.2 Build order ke usool

- Pehle DB migration, phir backend endpoint + test, phir frontend page.
- Har integration (Claude API, Instantly) alag module mein, bina UI ke test ho sake.
- Real calls ke tests opt-in (env flag), default tests offline aur deterministic.
- Stage 7 tak `SEND_MODE=test`; live sending sirf user ki explicit ijazat se.

### 7.3 Test data

Koi fake business data repo mein nahi. Testing ke liye user apni asli (ya sample jo user de) CSV/Excel file deta hai; unit tests chhoti synthetic rows use kar sakte hain jo sirf test fixtures hon aur UI/DB mein kabhi seed na hon.

## 8. Risks aur Open Questions

| Risk | Asar | Bachao |
| --- | --- | --- |
| Instantly mein direct single-email send na ho | Sending design badalni pare | Stage 7 se pehle docs verify; campaign/sequence based design ready rakho |
| AI facts invent kare | Galat claims wali emails | Strict prompt (sirf lead data), AI + rule risk check, MEDIUM/HIGH review |
| Spam / deliverability | Domain reputation kharab | Warm-up Instantly se, chhote batches, test mode pehle, risk check ko guarantee na samjho |
| Webhook local mein na pahunche | Replies nazar na aayen | Tunnel + manual Refresh fallback |
| Badi file par timeout | Import fail | Backend chunked processing, background task |
| Claude API rate limit/cost | Run adhoora | Retry, lead-by-lead status, resume option |

### Tay ho chuki baatein aur baqi sawal

- Tay: campaign mein max 10 email steps.
- Tay: lead file ke 11 columns (upar schema mein map kiye).
- Tay: client ka Instantly account maujood hai, kuch sending emails connected hain.
- Tay (superseded): OpenRouter par free model. Claude API ab approved provider direction hai; rotated key, configured model aur client budget secure backend config mein required hain.
- Tay: target UK cafés aur restaurants; email mein PocketPerks Loyalty Stamp ka pitch.
- Tay: Apify ke through lead discovery abhi manually hoti hai; user CSV/XLSX import karega. Apify API integration abhi nahi karni.
- Tay: har marketing email mein sender identity aur opt-out tareeqa hoga; opt-outs future sends se suppress honge. Individual subscriber eligibility ko send se pehle handle karna hoga.

* [ ] Client se sender postal address/footer format confirm karna.
* [x] Removed the global 4-hour override. Instantly sequence steps now use the saved `campaign_steps.delay_days` value (step 1 is 0; later steps currently default to 3 days). Instantly applies the campaign's daily sending schedule to each step; provider pacing and mailbox daily limits control how leads are spread inside that window.
* [x] Sample lead file (9 rows) controlled pilot mein import hui.
* [x] Instantly API v2 account/key configured; secret ko expose na karein.
* [x] Instantly v2 sequence planned steps support karta hai; lead sync 1000-provider maximum se chhote chunks mein hota hai.


### Campaign scheduling requirements (confirmed)
- Campaign start/end date-time browser ke local IANA timezone ke saath save hote hain; backend absolute timestamps store karke timezone Instantly ko deta hai. Migration `016_campaign_datetime_window.sql` ke baad exact-time activation aur end-time pause workers implemented hain. Local Docker host online rehna zaroori hai, warna PocketPerks lifecycle actions late ho sakte hain.
- Instantly sequence delay comes from each saved campaign step (`delay_days`); never replace it with a global fixed-hour value or set a follow-up to zero. Instantly applies the same active daily sending window to every step and spaces each lead's messages according to its configured delay. Estimate total sequence volume against selected mailbox daily limits, sending gaps, weekdays and saved follow-up delays. Block a new run when the selected campaign window is too short, at both the frontend and API, and show the approximate minimum duration. Never activate before the start time or continue past the end time.
- Sending must use a durable database-backed queue/scheduler and idempotent worker so queued messages survive app/browser closure, server restarts, and worker retries. Never mark `sent` until Instantly confirms a successful send.
- Starting a campaign chosen imported batch, approved campaign aur selected sending inboxes ka backend handoff hai: personalization, risk routing, lead sync, scheduling aur (explicitly enabled hone par) provider activation backend mein hote hain. Browser close karne se kaam cancel nahi hota. Live mode sirf controlled recipients ke liye client approval ke saath rakhein.
- Replies must sync into the PocketPerks inbox and be linked back to the lead/campaign/email when possible. Reply sync remains part of Stage 7 delivery completion.
- Every email includes sender identity and a clear opt-out route. Persist opt-outs in a suppression list and exclude them from later sends. Before live sending, classify/confirm subscriber eligibility; the opt-out route does not itself authorize marketing to individual subscribers.
- The current text-level spam check can flag obvious wording/format patterns but cannot predict Gmail Inbox versus Spam placement; reputation, authentication, engagement, and provider-side signals are outside the app's text-only check.
- The text-only risk estimate specifically checks deceptive urgency, unrealistic promises/prizes, coercive click/account-verification language, financial bait, fake reply/forward subjects, excessive capitals/punctuation, unusually long copy, and multiple links. LOW is queued automatically; concrete MEDIUM/HIGH content signals are shown for human review.
- Campaign targeting is derived from the selected lead batch and campaign instructions. There is no separate campaign city field. Instructions should not constrain the campaign to one city unless the imported batch is intentionally limited to that city.

### Internal authentication follow-up checklist
- [x] Invite-only sign-in and password recovery UI; backend API authentication and Admin/Operator/Reviewer role enforcement are implemented.
- [x] Admin account and Operator account were provisioned by the user; Operator access was checked by the user.
- [ ] Reviewer account invitation, `reviewer` role assignment, and permission check (Dashboard + Human Approval allowed; Import Leads/Campaigns denied). Deferred by the user.
- [ ] Confirm local password reset redirect allowlist and complete a real sign-in/password-recovery smoke check after the UI updates.
- [ ] Re-run full backend pytest and frontend production build in the user's local Windows environment; current Codex run was blocked/stalled by environment issues.

### Next implementation slice
- Stage 7.1 is complete: read-only Instantly client and live account-list check succeeded (6 accounts); no sends were made.
- Stage 7.2 is complete: official API v2 docs verify campaign creation/activation, lead batch sync (up to 1000 per call), step custom variables, date/window scheduling, follow-up delays, webhooks and required scopes. Instantly leads sync into the selected campaign while Supabase remains the PocketPerks record of truth.
- Stage 7.3 draft/lead sync is implemented: the durable worker creates an inactive provider draft, maps only leads whose complete personalized sequence is `queued`, uses per-lead subject/body merge variables, and records provider IDs/statuses in Supabase. Unknown write outcomes are held from automatic resend. Earlier implementation history is in `pocketperks/log.md`.
- Opt-out/suppression implementation is now in code: migration 019 adds a global normalized-address suppression list; clear opt-out replies are recorded automatically, including already-saved replies and Gmail inline quoted replies; reviewers can mark ambiguous replies as opt-outs; new imports, personalization work and Instantly lead sync skip suppressed addresses; synced message bodies receive a plain-text `reply STOP` route. Apply migration `pocketperks/supabase/migrations/019_email_suppressions.sql` before restarting the backend. This has not been tested or deployed. Instantly sequences already stop on any reply, but this app does not call an Instantly blocklist endpoint; existing provider-side recipients must still be checked/reconciled if an opt-out is recorded after lead sync.
- Stage 7 auto-launch is implemented behind two independent runtime gates: `SEND_MODE=live` and `INSTANTLY_AUTO_LAUNCH=true`. Defaults remain test/off. The worker waits until the campaign's timezone-aware start timestamp, verifies approved generic steps, complete queued personalized sequences, no active suppression, and confirmed lead sync, then activates the Instantly campaign. Ambiguous activation responses are recorded as `launching` and require provider reconciliation; they are never blindly retried. Migration `022_instantly_auto_launch_state.sql` adds that state. A separate backend worker pauses campaigns after the exact end timestamp. Because the current backend runs on the user's Docker/local host, exact-time pause can be delayed while that host is offline. Instantly receives the campaign's local start/end dates, timezone, and daily time window.
- Live-mode pilot update (2026-11-06): the user explicitly enabled both runtime gates for controlled test recipients and rebuilt the Docker backend. The user confirmed automatic activation at 09:00 Asia/Karachi, then reported all 9 emails sent: 7 Inbox, 2 Spam. This validates the small live path only, not bulk throughput or inbox placement. `Active` alone is not proof that messages were delivered.
- Stage 8 inbox now includes authenticated reply listing/read state, Instantly polling, bounded cursor continuation, idempotent storage, unmatched-reply visibility, and an in-app reply action restricted to an existing Instantly thread. Reply sending requires the Instantly `emails:create` scope and migration 029 to store the original sender mailbox. Provider-side mark-read and a complete multi-message thread timeline remain outside the current UI.
- Reply inbox now separates the newest reply text from quoted history and supports read-state, campaign, received-date and search filters. Dashboard chart endpoints aggregate 14-day lead/reply activity and current personalized-email statuses in Postgres; apply `pocketperks/supabase/migrations/018_dashboard_activity.sql` to enable chart data. No provider sending actions were added.
- PocketPerks now polls Instantly's read-only sent-email history for mapped campaigns and marks a local `queued` draft `sent` only when campaign, mapped lead (or recipient fallback) and subject match a provider sent record. The dashboard queued metric now means awaiting provider confirmation. The send-status poll shares the existing low-frequency inbox worker and stays below the email-list API rate limit; it does not activate or send campaigns.
- Sending estimates in Start Campaign use the selected inbox daily limits/gaps and the batch size to show approximate first-touch capacity and the saved follow-up delay; the estimate uses all lead-step emails, selected inbox daily limits/gaps, weekdays and saved follow-up delays. Starting is blocked when the campaign window is too short, at both the frontend and API. Estimates remain approximate because Instantly controls actual sending. This is a planning estimate, not a delivery or inbox-placement guarantee.
- Instantly sync recovery now revisits mappings left in `syncing_leads`, including `unknown` lead writes. If every run lead has a persisted `synced`/`skipped` outcome and there are no pending, unknown, or failed outcomes, the status endpoint reports `leads_synced` even if a worker stopped before writing the final campaign status. Recovery then finalizes the durable mapping without reposting provider-confirmed leads.

### Suppression release and audit controls
- Added migration 020 for active/released suppression state and an event audit table; it backfills the existing suppression list. Backend RPCs make suppression/reactivation and audited admin release atomic.
- Admins can inspect the global suppression list and recent history in Lead Replies. A release requires explicit recipient re-consent plus an audit reason; it only removes the global send block and does not add the address to an already active Instantly campaign. Operators/reviewers cannot access these controls.
- Next: apply migration 020 in Supabase, then rebuild/restart the backend and redeploy the frontend. Verify automatic STOP suppression and admin re-consent release with controlled test addresses before any live campaign. Tests/build were not run for this update.

### Suppression release RPC correction
- Migration 021 fixes a PL/pgSQL name collision in the audited suppression release function introduced by migration 020. Apply it after migration 020, then restart the backend before retrying the admin release action.

### Lead reply deletion
- Added admin-only deletion from Instantly Unibox and the local reply view. It deletes the provider email first, preserving the local record if Instantly rejects the request. This does not remove a copy already delivered to the recipient's mailbox.
- Instantly requires `emails:delete` for this API. The configured key currently has `emails:read`, so make a new least-privilege API key that retains the existing required scopes and adds `emails:delete`, then update the backend key. Redeploy/restart the backend after that key update.

### Current remaining work and scale plan (2026-11-06)
- Added a hard unresolved-placeholder gate: Human Approval refuses `[Your Name]`/`{{name}}`-style tokens until edited; personalization instructs the model to use `The PocketPerks Team` when no real sender name is provided; Instantly payload creation rejects any leftover placeholder. This protects future sends and cannot recall messages already delivered.
- The 9-recipient live pilot is complete as a controlled workflow check (user-reported 7 Inbox, 2 Spam). A 200-lead rehearsal does not require 200 inboxes: use synthetic leads and mocked providers in an isolated local run to evaluate import, batching, personalization progress/retries, suppression, mappings and duplicate prevention. Do not send synthetic data through the live-connected Instantly workspace. Actual deliverability checks should use only inboxes the user controls or has permission to test.
- Before scaling, complete and record the isolated 200-lead mock rehearsal (duration, chunk outcomes, retries/resume, no duplicate provider writes). A small separately approved live pilot can validate delivery; a 200-row import/sync cannot prove inbox placement.
- Production Claude availability is a client dependency: use a rotated funded API key, approved model and spending limit. The application selects the key/model through backend environment variables; do not silently switch models or fall back to OpenRouter.
- Feature readiness estimate: approximately 80% of the planned product behavior is implemented; this is a project estimate, not a test-coverage score. The small live pilot proves the basic send path, not production-scale reliability or inbox placement.
- Lead eligibility now runs before AI personalization and again before Instantly sync. Default `LEAD_ELIGIBILITY_MODE=advisory` allows legacy or unverified rows but holds explicitly invalid/risky flags; optional `strict` mode also holds anything not explicitly marked valid. Accepted verifier labels still need client agreement before switching to strict.
- Instantly webhooks now have a shared-secret-authenticated intake endpoint, digest-based duplicate suppression, durable Supabase queue, retrying worker, reply/unsubscribe event handling, and successful-event payload cleanup. Existing one-minute polling stays enabled as a fallback. Migration 029 is required; webhook registration itself must wait until the client-provided server has stable HTTPS and a configured `INSTANTLY_WEBHOOK_SECRET`.
- Remaining readiness work: the isolated 200-lead mock rehearsal (explicitly deferred until the paid AI model is available), final backend/frontend validation and controlled reply-send verification with the `emails:create` scope, and then deployment/monitoring/secrets on the client-provided production server. Do not send synthetic leads through the live-connected Instantly workspace.
- Client decisions needed for safe feature completion: provide sender postal/footer details and confirm the verifier's accepted valid-result labels and blocking quality flags before enabling strict eligibility. Reviewer account invitation/permission check remains deferred unless the client needs that role.
- External operating dependency: use a client-funded Claude API key/model with an agreed budget; configure both as backend secrets/environment variables. The Claude integration supports model selection through configuration.
- Deployment is a separate later step, as the client will provide the production server. Before go-live, also verify end-time pause with the worker online, configure monitoring/backups/secrets on that server, and keep the backend reachable without a temporary tunnel. Stage 9 feature sign-off remains open.

## Stage 4.3 — Customer-facing copy guardrails (2026-11-08)
- Added a shared backend validator to generic draft generation, draft regeneration, and per-lead personalization. It detects internal kit/source/prompt references and sector wording that conflicts with a neutral brief or the verified lead category.
- Invalid generated copy receives one Claude correction attempt. If it still fails, generation is stopped with a safe error; personalized copy is not saved as queued. The existing human approval, suppression, scheduling, and Instantly flow are unchanged.
- Personalization now explicitly adapts generic sector language to the imported business category; missing or ambiguous categories stay sector-neutral. Added restaurant framing based only on general approved product facts.
- Added a defense-in-depth high-risk flag for internal-source wording in the existing content risk scan, including manually edited copy.
- Backend syntax compilation passed. No database migration or frontend change is required. Automated tests, deployment, and push have not yet been run/performed.

### Email workflow aligned to the Harrogate kit (2026-11-08)
- Optional lead first-name import with fallback greeting order: first name, business name, “Hi there”. Per-lead prompts continue to adapt to the imported café, restaurant, barber or sandwich-shop category in mixed batches and keep missing categories neutral.
- Email 2 defaults to a 3-day wait in both UI and API. Later steps support timed follow-ups, a no-reply close-loop step (Instantly stops the sequence on reply; default 12-day wait after Email 2 approximates day 15), and a manual after-visit/conversation trigger.
- Manual conversation follow-ups are omitted from the scheduled Instantly sequence. A reviewer must confirm the real event and invoke a thread-bound send; durable state blocks duplicate sends and ambiguous results require reconciliation.
- Configurable sender/company/website footer and optional phone; canonical opt-out text is “reply 'no thanks'” and the reply parser maps it to the global suppression behavior. Only approved product facts are allowed; unconfirmed review, reporting and pilot claims stay excluded.
- Non-email Harrogate material remains a later sales-playbook scope. Apply migrations `032_email_workflow_profile.sql` and `033_manual_conversation_email.sql` in order. Local Python syntax compilation and frontend TypeScript typecheck passed; automated tests and live sends were not run.
- Correction: Instantly's step delay controls the following step, so each saved follow-up wait must be shifted onto the previous provider step (e.g. Email 2's 3-day delay belongs on the Email 1 payload). Standard `company_name` mapping is retained, with `pp_business_name` also supplied as a visible custom field. Existing campaigns/lead rows are not retroactively updated; verify with a new controlled campaign after backend restart.

### Import cleanup provider reconciliation (2026-11-08)
- Before previewing or deleting an import batch, the backend now reads each linked active/scheduled Instantly campaign by ID using the existing `campaigns:read` permission.
- Provider-deleted campaigns are reconciled locally as completed; provider draft, paused, active, and completed states are mapped to their local lifecycle states. Unknown statuses or provider/API errors fail closed and leave cleanup blocked.
- The final database cleanup RPC remains authoritative and still blocks deletion while a campaign can send. Cleanup preview refreshes after reconciliation so the UI receives current local state.
- No migration or frontend change is required. This reconciliation only runs for a batch with a linked active/scheduled provider mapping; campaign sending, scheduling, and lead sync behavior are unchanged.

### Duplicate campaign-run protection (2026-11-08)
- A campaign and imported batch pair can start only once. The backend checks prior runs and returns HTTP 409 with a clear explanation instead of generating another set of drafts or another Instantly campaign.
- The Start Campaign button locks that same pair after a successful start for the current page session. After refresh/reopen, the backend remains authoritative and blocks the repeat.
- A different imported batch can still run with the approved campaign. No migration is required; the existing unique active-run index continues to protect simultaneous requests.

## Current readiness review (2026-11-08; supersedes earlier estimates/checklists)

### Current assessment
- Local feature readiness: approximately **90%**. Main workflow is implemented and has passed controlled small-batch live use; yeh engineering estimate hai, test-coverage metric nahi.
- Production readiness: approximately **65%**. Production sign-off abhi baqi hai kyunki final regression, scale rehearsal, aur always-on hosting/operations verify nahi hue.
- Core workflow implemented: lead import/duplicate checks; campaign setup and generic draft review; Claude generation and per-lead personalization; risk review and suppression; Instantly campaign/lead sync and gated auto-launch; send-status polling; reply sync and thread reply; dashboard; import cleanup; duplicate-run protection.
- User-reported live checks: 9-recipient test mein 7 emails Inbox aur 2 Spam mein land hui; baad ke 4-lead/two-step test mein chaar leads Instantly sync hui. Yeh small controlled path ko validate karta hai, bulk throughput ya Inbox placement ko nahi.

### Local feature sign-off baqi
- Latest GitHub changes ko backend mein rebuild/restart karke confirm karo; latest frontend deployment bhi verify karo. Duplicate-start aur stale Instantly mapping fixes 2026-11-08 ko push hui hain, magar unka deployed runtime status abhi confirm nahi.
- Latest code par full backend pytest suite aur frontend production build chalao. Aakhri user-reported full backend result 107 passed / 3 skipped tha, latest fixes se pehle; un fixes par syntax/diff checks hue, full suite nahi.
- Isolated 200-lead mock rehearsal complete karo: chunks, progress, restart/resume, retry, suppression, duplicate protection, aur provider write duplication. Synthetic recipients ko live-connected Instantly workspace mein mat bhejo.
- Intended Supabase project mein migrations 029–033 applied hone ki tasdeeq karo. Yeh webhook events, per-run end time, sending schedule/delays, sender profile aur manual conversation follow-ups ke liye hain.
- Ek controlled live sequence par Email 2 ka 3-day wait, provider-confirmed send statuses, reply sync aur opt-out suppression dobara verify karo. Email Inbox/Spam placement app guarantee nahi kar sakti.

### Production deployment se pehle baqi
- Client ke always-on HTTPS server par deploy karo; backend secrets configure karo, jin mein funded Claude key/model, Supabase service key, Instantly API key aur webhook secret shamil hain. Secrets frontend/repository mein nahi jane chahiye.
- Stable HTTPS endpoint milne ke baad Instantly webhook register karo; polling fallback rehne do, aur deployed environment mein signature validation, retry aur duplicate-event handling verify karo.
- Server ko campaign activation aur exact end-time pause ke waqt online rehna chahiye. Logs/alerts, database backup/restore, aur restart/rollback process configure karo.
- Lead verifier ke accepted valid labels/blocking flags aur sender footer/contact details client se confirm karo; tab tak strict eligibility enable na karo.
- Reviewer invitation/role smoke check optional aur deferred hai jab tak reviewer account operationally zaroori na ho.

### Release decision
- **Local MVP:** nearly feature-complete; formal sign-off se pehle final regression aur 200-lead isolated rehearsal baqi hain.
- **Production:** abhi complete nahi. Always-on deploy, webhook registration, secrets/monitoring/backups aur final checks verify hone ke baad hi production-ready mark karo.

### Public landing page (2026-11-09)
- Added a public PocketPerks outreach landing page at `/` with a concise product overview, four workflow stages, reserved demo-video area, FAQs and footer navigation.
- The existing authenticated application remains available at `/workspace`; `/login` uses the existing Supabase sign-in and routes signed-in users into the workspace. Account and password recovery routes remain behind the same auth gate/flow.
- Landing copy documents CSV/XLSX import and duplicate/invalid/suppression checks, Claude personalization, risk-based human review, Instantly campaign activation/scheduling, provider-confirmed sends, replies and future-sync suppression. It clarifies invite-only access and avoids promising inbox placement.
- The public route renders without waiting for session restoration. No database migration or backend changes are needed. Frontend TypeScript check and production build pass. Deploy the frontend to publish the page; add the approved demo video when ready.

### Dashboard run history and approval-history controls (2026-11-09)
- Dashboard now loads the 12 most recent campaign runs and lets the user select one; selected-run details show date, status, processed leads, queued/waiting emails, confirmed sends, reviews, and failures. Details for earlier runs load on demand.
- Human Approval history uses simple text and date-range filters with server-side pagination. Admins can clear the full audit log after confirmation; reviewers cannot clear it. This removes only review-history rows and leaves emails, pending reviews, leads, and campaigns unchanged.
- Confirmed the intended approval rule remains unchanged: LOW-risk generated emails move to the send queue (or remain approved for manual conversation steps); MEDIUM/HIGH emails enter Human Approval. An empty queue is expected when generated emails are rated LOW.
- No database migration is required. Backend Python compilation and frontend typecheck passed. Backend tests were not run in this update. No live email was sent.
