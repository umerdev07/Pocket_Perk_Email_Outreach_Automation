# PocketPerks – Planning Pack (PRD, TRD, App Flow, UI/UX, Schema, Plan)

Oct 31, 2026 · @Umer

## 1. Overview

PocketPerks ek simple email outreach automation tool hai: leads import karo, campaign banao, AI se drafts likhwao, har business ke liye personalize karo, risky emails ko human approval do, Instantly se bhejo aur replies ek jagah dekho. Yeh CRM nahi hai.

**Core pipeline:** Import Leads → Campaign + generic drafts (OpenRouter) → Draft approval → Start Campaign (per-lead personalization) → Risk assessment → Human Approval (MEDIUM/HIGH) → Instantly send → Lead Replies.

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
| CP-3 | Create Draft Campaign | Campaign save + OpenRouter se har step ka generic draft |
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

Frontend sirf FastAPI se baat karta hai; FastAPI Supabase, OpenRouter aur Instantly se. Koi secret browser tak nahi pahunchta.

### 3.1 Stack

| Layer | Technology | Zimmedari |
| --- | --- | --- |
| Frontend | Next.js + TypeScript (App Router), Tailwind | 4 pages, forms, tables |
| Backend | FastAPI + Python 3.11+, Pydantic | Business logic, APIs, background jobs |
| Database | Supabase PostgreSQL | Saara data; backend service key se access |
| AI | OpenRouter API | Generic drafts, personalization, risk assessment |
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
      integrations/ openrouter_client.py instantly_client.py
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
- **OpenRouter client:** ek module, model name `.env` se (`OPENROUTER_MODEL`), JSON output mode, timeout aur retry (max 2).
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

Instantly ko sirf actual email delivery, provider status aur replies ke liye use karna; lead discovery/Apify integration abhi scope mein nahi. Apify research user manually karega, phir apni CSV/XLSX lead file PocketPerks mein import karega. PocketPerks se Instantly tak har eligible business ki approved personalized subject/body aur recipient jayegi; Instantly mein leads ko bulk-import karna product requirement nahi hai.

Instantly mein standalone "send one email" endpoint par bharosa nahi karna. Implementation se pehle official API docs padh kar sending mechanism confirm karo: har lead ka apna personalized subject/body, campaign scheduling, follow-up timing, status callbacks aur minimum write scopes. Final design ko docs verification ke baad yahan update karo.

**Client ka Instantly account:** API key ka read-only test ho chuka hai aur 6 connected sending accounts list hue. Yeh sirf account-read verification tha; is se sending capability ya email delivery verify nahi hoti.

- `SEND_MODE=test` (default): koi Instantly call nahi, email DB mein `sent_test` mark; UI par "TEST" badge.
- `SEND_MODE=live`: real Instantly calls; asli response save hota hai, error aaye to status `failed` + error text.

### 3.6 Replies

- Primary: Instantly webhook (reply event) → `POST /api/webhooks/instantly`.
- Fallback: manual "Refresh" button jo Instantly se replies fetch kare.
- Local mein webhook ke liye tunnel (ngrok/cloudflared) chahiye; match lead email + Instantly campaign/lead id se.
- Opt-out/rejection replies ko record karke suppression list mein daalne ka workflow define karo; ambiguous natural-language replies ko automatic opt-out samajhne ke bajaye human review mein bhejo.

### 3.7 Environment variables (backend `.env` only)

`SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, `INSTANTLY_API_KEY`, `SEND_MODE`, `FRONTEND_ORIGIN`. Frontend mein sirf `NEXT_PUBLIC_API_URL`.

**OpenRouter free model:** `OPENROUTER_MODEL` mein koi free model (id ke aakhir mein `:free`) rakhein; asli list OpenRouter par dekh kar Stage 4 mein tay hogi. Free models par rate limit aur quality kam ho sakti hai, isliye requests ek ek kar ke, retry/backoff ke saath, aur risk check + human review ko aur zaroori samjhein.

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
- OpenRouter/Instantly timeouts par retry, phir clear failure state.
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
| delay\_days | int | default 0 (step 1), baqi default 3 |

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

| Stage | Kaam | Deliverable | Exit check |
| --- | --- | --- | --- |
| 1. Foundation | Naya folder, FastAPI + Next.js setup, Supabase connection, initial migration (8 tables), `/api/health`, CORS, `.env.example` | Dono apps local chalte hain, frontend health call dikhata hai | `pytest` health test pass; frontend se backend + DB connection confirm |
| 2. Import Leads | Upload endpoint, CSV/.xlsx parse, normalize, email/domain duplicate check, summary, preview (20 rows), Import Leads page | Poori file DB mein, preview 20 rows | Tests: duplicates, invalid email, 100+ rows file mein preview sirf 20, empty/galat file error |
| 3. Campaign Management | Campaign name, instructions, total emails, per-step contexts, campaign start/end dates; campaign + steps save/list; delete a step while campaign remains draft | Campaign banta, date range save hoti aur list mein dikhti hai | Date order and required-field validation; deletion preserves at least one step and re-approval |
| 4. OpenRouter Drafts | OpenRouter client, generic draft generation, draft edit/regenerate/approve, campaign status `approved` | Har step ka approved draft DB mein | Real OpenRouter call (key ke saath) se draft; edit ke baad re-approve; unapproved campaign start nahi hota |
| 5. Personalization | Start Campaign endpoint, durable PGMQ run queue, restart-resumable worker, bounded page/chunk processing, per-lead per-step personalization, run progress and per-step failure details | `personalized_emails` rows har lead x step ke liye; saved lead/step pairs retry par reuse; missing steps ki safe reason run report mein | Apply queue migration; test batch par sahi lead/campaign/step link; restart/retry par duplicate email rows ya unnecessary repeat AI calls nahi; ek lead fail hone par run nahi rukta; failures explainable; koi invented fact nahi (sample review) |
| 6. Risk + Human Approval | Lightweight content-level spam-risk checks, LOW/MEDIUM/HIGH routing, approval/review history, edit/approve/reject, risk recheck | Only obvious content/format spam signals need human review; LOW goes to queued (not sent) | Risk checks do not claim Gmail inbox placement; history records automated checks and human decisions |
| 7. Instantly + durable scheduling | Official Instantly API verify; persistent DB-backed queue/scheduler, step delays, date-window checks, restart-safe workers, test mode, status tracking | Scheduled email steps survive app/browser restarts and only send within campaign dates | No duplicate sends after worker restart; step delay enforced; test mode makes no live send; real send needs explicit authorization |
| 8. Lead Replies | Webhook + refresh, reply matching, replies table, inbox UI | Reply inbox mein dikhta hai | Duplicate webhook se double reply nahi; unmatched reply bhi dikhe; thread view sahi |
| 9. Final testing | Poora 15-step workflow, UI polish, error states, README | Local MVP signoff | Full flow pass, backend + frontend tests green, har button verified |

### 7.1 Har stage ke baad report

Format: **Bana kya** · **Tests (pass/fail)** · **Baqi kya** · **Koi decision jo user se chahiye**.

### 7.2 Build order ke usool

- Pehle DB migration, phir backend endpoint + test, phir frontend page.
- Har integration (OpenRouter, Instantly) alag module mein, bina UI ke test ho sake.
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
| OpenRouter rate limit/cost | Run adhoora | Retry, lead-by-lead status, resume option |

### Tay ho chuki baatein aur baqi sawal

- Tay: campaign mein max 10 email steps.
- Tay: lead file ke 11 columns (upar schema mein map kiye).
- Tay: client ka Instantly account maujood hai, kuch sending emails connected hain.
- Tay: OpenRouter par free model.
- Tay: target UK cafés aur restaurants; email mein PocketPerks Loyalty Stamp ka pitch.
- Tay: Apify ke through lead discovery abhi manually hoti hai; user CSV/XLSX import karega. Apify API integration abhi nahi karni.
- Tay: har marketing email mein sender identity aur opt-out tareeqa hoga; opt-outs future sends se suppress honge. Individual subscriber eligibility ko send se pehle handle karna hoga.

* [ ] Sender address ka matlab: kya email ke neeche business ka naam + address footer chahiye? Format kya ho?
* [ ] Steps ke beech default delay 3 din theek hai?
* [ ] Sample lead file (chhoti 5-10 rows) bhej dein taake import test ho sake.
* [ ] Instantly API key aur kaun sa plan (API v2 access)?
* [ ] Steps ki sankhya 10 tak: sending sequence mein Instantly par bhi itne steps allowed hain? Stage 7 mein verify hoga.


### Campaign scheduling requirements (confirmed)
- Campaigns have a start date/time and end date/time in the UK timezone. The current implementation stores date fields only; they do not trigger sends yet, so time-of-day scheduling and timezone handling still need implementation/migration.
- Once sending is implemented, spread the campaign's available window across its email steps for each lead, respecting any configured follow-up delay and provider limits. Never send before the start time or after the end time. If the window cannot fit the configured steps safely, hold the run and show a clear reason instead of sending all steps together.
- Sending must use a durable database-backed queue/scheduler and idempotent worker so queued messages survive app/browser closure, server restarts, and worker retries. Never mark `sent` until Instantly confirms a successful send.
- Starting a campaign is intended to be the one-click handoff for the chosen imported batch and approved campaign: personalization, risk routing, scheduling, and provider sends happen in the backend. Closing the browser must not cancel this work. Keep `SEND_MODE=test` until the full provider path and user authorization are verified.
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
- Stage 7.2 is pending: review official Instantly API docs for per-lead personalized subject/body, campaign/sequence mechanics, scheduling/follow-ups, reply sync, and minimum write scopes. Apify API integration is explicitly out of scope for now.
- Stage 7.3 will implement the test-mode, durable one-click sending workflow, opt-out/suppression handling, send-status tracking, and scheduled follow-ups. Stage 8 will complete reply ingestion/inbox integration if not covered by the verified Instantly path. Do not enable live sends until documentation, eligibility checks, opt-out handling, and user authorization are in place.
