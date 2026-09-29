# Job Search Agent

A personal pipeline that finds early-career ML and AI engineering roles, ranks them against one résumé, and writes a
tailored résumé, cover letter and form answers for the best matches. Its hard rule is that nothing may be claimed that
the master résumé does not support. Every automated submission needs a recorded human approval.

I built it for my own job search with [Claude Code](https://claude.com/claude-code). "Russ" in the prompts, the command
help and `docs/SUBMITTING.md` is me, the candidate. Deterministic Python does the fetching, filtering, validation,
gating and rendering. LLM agents (Claude Code workflows) make the judgment calls: scoring fit, drafting, and
adversarially fact-checking the drafts.

## How a daily run works

```
 fetch ─► prepare ─► score ─► skeptic ─► merge ─► pick ─► draft ─► fact-check ─► fix ─► re-check ─► build ─► digest
 (py)      (py)      (LLM)     (LLM)      (py)     (py)    (LLM)     (LLM)        (LLM)   (LLM)       (py)     (py)
                                                    │                             │
                                       live application form             check-app: schema,
                                       fetched from the ATS               form coverage, writing rules
                                                                          and the no-fabrication guard
```

1. **Fetch** (`fetch_jobs.py`) pulls open postings from about 300 public job boards (302 of the entries in
   `data/companies.json`). The Greenhouse, Lever, Ashby and SmartRecruiters APIs list every posting, and Workday boards
   are searched by ML keywords. Postings are screened on title, seniority keywords and location. Every live URL is
   recorded, so a posting that disappears is treated as closed.
2. **Prepare** (`daily.py sweep` runs fetch and prepare) drops postings already seen and clear mismatches: 5+ years
   required, eligibility requirements the candidate cannot meet, non-engineering titles, and postings older than
   `max_posting_age_days`. Descriptions are trimmed to the paragraphs that matter, so a batch of 20 fits in one agent's
   context. At most `max_batches_per_run` batches are scored per day, newest first, which keeps each day's compute bounded.
3. **Score** (`.claude/workflows/job-score.js`). One agent per batch scores fit from 0 to 100 with a written rubric and
   cites résumé evidence. A second, deliberately **skeptical agent** re-reads every posting that scored high enough to be
   picked and tries to refute the score (hidden seniority, unmet must-haves, eligibility, location). It reads only those
   postings, which `daily.py skeptic-input` extracts with their first-pass scores. Agent output is
   validated by `daily.py check-scored` and `check-verdicts`. Invalid files are skipped and their postings retried.
4. **Pick** (`daily.py pick`) ranks by the post-skeptic score, applies caps (per company, per day, salary floor), confirms
   the posting is still live, and **fetches the real application form** (Greenhouse, Ashby and Lever) so answers are
   written for the questions actually asked. Questions only the human can answer are flagged. A form that requires a
   one-way video interview disqualifies the posting.
5. **Tailor** (`.claude/workflows/job-tailor.js`): draft, adversarial fact-check, fix, then a **final re-check** by a fresh
   agent. The fix pass must apply every fabrication and overclaim finding and pass `daily.py check-app`. The re-check
   reads the file that was actually written. One targeted repair is allowed, after which it must be clean. Drafting, the
   first review and the fixes run on Sonnet. The final re-check keeps the session model. Everything mechanical (schema,
   form coverage, select options, writing rules) is checked by `check-app`, so the agents spend no tokens on it. Jobs are
   tailored two at a time, so a run that hits the usage limit keeps the applications it finished.
6. **Build** (`build_all.py`) renders the résumé and cover letter to .docx and PDF through Microsoft Word, fits the résumé
   to one page, and writes a report per application.
7. **Digest** (`daily.py finalize`) re-runs `check-app --final` on every drafted application, sets tracker statuses and
   writes a summary: what is ready, which questions need an answer, what failed. A failed draft can be picked again on a
   later day if it still ranks (two attempts at most).

## How the human stays in the loop

- **Nothing is sent without an explicit yes.** The digest numbers each ready application. The reply in the Claude Code
  chat ("go", "approve 1 2", "skip 3", plus answers to open questions) is recorded with `daily.py approve KEY`. Only then
  can `.claude/workflows/job-submit.js` start.
- **The approval is bound to the content.** `approve` stores a hash of the fact-checked application. If anything changes
  afterwards, the gate refuses until it is re-checked and approved again.
- **A deterministic gate before the browser opens.** `daily.py can-submit` checks every key: status ready, approved for
  this version, documents built for this version, final re-check clean, nothing waiting on the human, never submitted
  before. `daily.py start-submit` repeats the check and marks the row `submitting` just before Submit is pressed, so a
  crashed run is never retried blindly.
- **Hard stops.** The submit agent stops at CAPTCHAs, account creation, passwords, verification codes, agreements beyond
  the usual privacy acknowledgement, and questions only the human can answer. Portals that need an account (Workday,
  SmartRecruiters) are never submitted by the agent. The candidate gets the documents and applies himself.

## Answers carry over between forms

New questions are the normal case, not the exception. Each answer is kept and reused:

- **Standing answers.** An answer given once in chat (`daily.py learn "question" "answer" --match REGEX`) is reused on every
  later form whose question matches, and applied at once to finished applications. Guards keep a reused answer off
  criminal-history questions unless it is the candidate's own criminal-history answer, off questions that ask two things
  at once, and off work-status questions unless the answer is exactly one of the form's options.
- **Verified facts.** A private facts file holds what forms ask for but a résumé does not show (education dates, years
  with each skill). When a drafter needs a missing plain fact, it may search the candidate's own documents read-only. The
  allowed folders are named in a private rules file, and the fact-checkers reject any learned fact whose cited source
  does not state it. If nothing has the answer, the question goes to the human once, in a deduplicated "Questions for
  you" list at the top of the digest.
- **Question bank.** The answers on each submitted form are kept in `private/question_bank.json` (contact, EEO,
  work-authorization and one-off answers are left out), so the next form that asks the same thing gets a consistent
  answer. `daily.py learn-question` checks and records a question that appeared on the live form but not in the fetched one.
- **Form mechanics.** When a form teaches the submit agent something new, it reports one sentence. `daily.py note` refuses
  anything that looks like a page's instruction, an agreement, sign-in or personal data, and keeps the rest privately
  for later submit agents. General lessons go into `docs/SUBMITTING.md` by hand.

## Writing rules

Everything written in the candidate's name follows his own writing rules (kept privately). `style_guard.py` enforces the
mechanical ones inside `check-app` on the cover letter, the résumé summary and drafted form answers: no em dashes or
semicolons, no banned words ("leverage", "passionate", "showcase"), no "not only X but also Y" or "It's not X, it's Y"
constructions, no sentence over 40 words, and no wording that reads as inability ("I don't have experience with..."). A
skills question gets the closest real experience instead of a bare "No". A Yes/No skills question with no defensible
"Yes" goes to the human. The fact-checker covers the rest (metaphors, clichés, forced groups of three).

## The no-fabrication guard

LLMs are good at making a résumé sound better than the person is. Two layers stop that:

- **`resume_guard.py` (deterministic, résumé only).** Every tailored bullet must cite the ids of the master bullets it
  came from. The guard rejects numbers that are not in those sources (including forms like "10x" or "$5M"), skills that
  are not in the master skills list, entry dates that are not in the master, and any change to the contact block.
- **Adversarial fact-check (LLM).** It catches what regexes cannot: a target metric stated as achieved, a dropped
  qualifier ("on a synthetic benchmark"), a design described as shipped, scope inflation ("led", "production"). It is
  the only check on the cover letter and free-text answers, and a second, fresh agent re-checks the final file. Findings
  are stored with each application, so every fix is auditable.

## Layout

| Path | What |
|---|---|
| `daily.py` | the deterministic pipeline: `sweep` (= `fetch` + `prepare`), `check-scored`, `skeptic-input`, `check-verdicts`, `merge`, `pick`, `add`, `check-app`, `stamp-recheck`, `finalize`, `answer`, `learn`, `learn-question`, `questions`, `note`, `approve`, `can-submit`, `start-submit`, `mark`, `status`, `stats` (`daily.py --help` lists them) |
| `fetch_jobs.py` | ATS board clients and screening |
| `forms.py` | live application-form fetcher (Greenhouse, Ashby, Lever), question classification, standing-answer matching |
| `make_batches.py` | mismatch filter, description trimming, batch files |
| `resume_guard.py` | the deterministic no-fabrication check |
| `style_guard.py` | the mechanical half of the writing rules |
| `render_resume.py`, `render_cover_letter.py`, `word_convert.py`, `build_all.py` | document rendering |
| `.claude/workflows/` | the LLM stages: `job-daily`, `job-score`, `job-tailor`, `job-submit` |
| `config.json` | public defaults: applications per day, score thresholds, per-company cap, batch size, posting age. The salary floor is 0 (off) here. `private/config.local.json` overrides any key |
| `data/companies.json` | the monitored job boards (302 fetched. A few careers sites without a supported API are kept with `ats: other`) |
| `docs/SUBMITTING.md` | the browser playbook for the submit step |
| `tests/` | `test_forms.py`, `test_style.py`, `test_workflows_ascii.py` (plain scripts, each prints `failures: 0`) and `integration_sandbox.py`, which runs the deterministic pipeline end to end on a throwaway copy with the LLM stages stubbed |

## What stays private, and why

`private/` and `data/` (except `data/companies.json`) are git-ignored. They hold the candidate's contact details,
work-authorization answers, salary expectations, EEO answers and the record of where he applied. None of that belongs in
a public repo. To run the pipeline you would create these yourself:

| File | What |
|---|---|
| `private/resume_master.json` | the master résumé: contact, education, experience and projects with bullet ids, skills |
| `private/profile.md` | target roles, locations, which eligibility requirements disqualify, known positioning risks |
| `private/application_answers.md` | standard form answers: names, links, availability, salary rules, EEO |
| `private/tailoring_rules.md` | rewording rules for this résumé and the name rules |
| `private/writing_rules.md`, `private/answer_bank.md`, `private/knowledge.md`, `private/search_rules.md` | writing rules, sample answers, verified facts, where drafters may look |
| `private/config.local.json` | overrides for `config.json`, the work-authorization policy (`auth_policy`) and the status wording |

## Running it

Windows only as written: PDF export drives Microsoft Word through COM (pywin32), and the workflows call
`.venv/Scripts/python.exe` from Git Bash with the project path set at the top of each workflow script. It needs Python
3.12+ and Claude Code. The submit step also needs Chrome with the Claude in Chrome extension.

```bash
python -m venv .venv && .venv/Scripts/pip install python-docx pymupdf pywin32
.venv/Scripts/python.exe daily.py fetch      # fetch and screen every board into data/runs/<today>/ (no LLM, no private files)
.venv/Scripts/python.exe daily.py status     # tracker summary
for t in tests/test_*.py; do .venv/Scripts/python.exe "$t"; done   # each prints failures: 0
```

The full daily run is the `job-daily` workflow, scheduled once a day in Claude Code. It needs the private files above.
`tests/integration_sandbox.py` also needs them and at least one earlier `daily.py fetch`.
