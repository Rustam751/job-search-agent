export const meta = {
  name: 'job-tailor',
  description: 'Draft, fact-check and fix a tailored resume, cover letter and form answers for each queued job',
  whenToUse: 'After `daily.py pick` or `daily.py add`. args: {date: "YYYY-MM-DD", jobs: [{key, brief}]}',
  phases: [
    { title: 'Draft', detail: 'one drafter per job writes private/applications/<key>.json and passes check-app --draft' },
    { title: 'Check', detail: 'adversarial fact-check that also fixes what it finds and passes check-app' },
    { title: 'Recheck', detail: 'fresh fact-check of the written file; one targeted repair, then it must be clean' },
  ],
}

const ROOT = 'D:\\Database\\Projects\\Job Search Agent'
const RUN = 'cd "/d/Database/Projects/Job Search Agent" && .venv/Scripts/python.exe daily.py'
const SEARCH = `${ROOT}\\private\\search_rules.md`
const KNOWLEDGE = `${ROOT}\\private\\knowledge.md`
// Usage: every agent reads ONE file, the job brief, which already carries the posting, the live form and the whole
// candidate context (master resume, rewording rules, standard answers, verified facts, writing rules, sample answers).
// Every extra turn re-sends an agent's whole context, so the prompts ask for one read and few command runs.
// Drafting and repair run on Sonnet; the adversarial passes keep the session model.
const WRITER_MODEL = 'sonnet'
const WORKER = 'job-worker'

// The slim job-worker agent type exists only in sessions started after .claude/agents/job-worker.md was added.
let slimOk = true
async function run(prompt, opts) {
  if (slimOk) {
    try { return await agent(prompt, { ...opts, agentType: WORKER }) } catch (e) {
      slimOk = false
      log(`agent type ${WORKER} unavailable, using the default agent: ${String(e).slice(0, 120)}`)
    }
  }
  return agent(prompt, opts)
}

const appFile = key => `${ROOT}\\private\\applications\\${key}.json`
const jobFile = (key, ext) => `${ROOT}\\data\\jobs\\${key}${ext}`

const FINDING = {
  type: 'object',
  properties: {
    severity: { type: 'string', enum: ['fabrication', 'overclaim', 'rule_violation', 'keyword_gap', 'style', 'length'] },
    where: { type: 'string', description: 'e.g. "summary", "<entry heading> bullet 2", "cover letter para 3", "field: sponsorship"' },
    problem: { type: 'string' },
    fix: { type: 'string', description: 'concrete replacement wording or instruction' },
  },
  required: ['severity', 'where', 'problem', 'fix'],
}

const RESULT = {
  type: 'object',
  properties: {
    key: { type: 'string' },
    written: { type: 'boolean' },
    check_ok: { type: 'boolean', description: 'true only if the last check-app run printed "ok": true' },
    remaining_errors: { type: 'array', items: { type: 'string' } },
    changes: { type: 'string', description: 'what this pass changed, briefly' },
    needs_russ: { type: 'array', items: { type: 'string' }, description: 'form questions left for Russ' },
  },
  required: ['key', 'written', 'check_ok', 'remaining_errors', 'changes', 'needs_russ'],
}

const RECHECK = {
  type: 'object',
  properties: {
    clean: { type: 'boolean', description: 'true only if there is no fabrication and no rule violation anywhere in the file' },
    findings: { type: 'array', items: FINDING },
    file_written: { type: 'boolean' },
  },
  required: ['clean', 'findings', 'file_written'],
}

const APP_SHAPE = `{"key": "<key>", "company": "...", "role": "...", "url": "<the posting url from the brief>",
 "resume": {"variant": "applied_ai|mle|cv_medical",
   "summary": "2-3 sentences, no first person, **bold** at most 2 key phrases",
   "skills": [{"label": "...", "items": ["..."]}],
   "sections": [{"title": "...", "entries": [{"heading": "", "heading_link": "", "link_label": "", "heading_right": "",
       "subheading": "", "subheading_right": "", "bullets": [{"text": "...", "source_ids": ["<master bullet id>"]}]}]}]},
 "cover_letter": {"date": "...", "company": "...", "role": "...", "greeting": "...", "paragraphs": ["..."], "closing": "Sincerely,"},
 "custom_answers": [{"question": "<exact form label>", "answer": "..."}],
 "field_answers": [{"question": "<exact form label>", "answer": "<exact option text for a select; empty when needs_user>", "needs_user": false}],
 "notes": "anything the human should check before submitting"}`

const RULES = `THE ONE RULE: every claim must be traceable to the master resume in the brief. You may select, reorder, shorten, merge and reword its facts, and use plain connective language. You may NOT add an employer, title, date, metric, tool, technology, scope or outcome that is not in the master. If the posting wants something the candidate lacks, leave it out of the resume - do not soften it into a half-claim.
The brief's "Rewording rules" list the mistakes a model tends to make with this master (stating a target as a result, dropping a qualifier, calling a design shipped, inflating a role's scope) and the name rules.
Work authorization is answered ONLY in structured fields, exactly as the brief's "Standard answers" say. Never write a citizenship, residency or visa status (such as "citizen", "permanent resident", "green card holder" or a visa type) anywhere, and never put work-authorization claims in the cover letter or free-text answers.
Every resume bullet must carry source_ids listing the master bullet ids it came from. Contact details are injected later - do not output them.
Length: the resume must fit ONE page - roughly 480-580 words of body text. 3-4 bullets for the most relevant entries, 2 for the rest; drop entries that do not serve this posting. Wrap at most two key phrases per bullet in **double asterisks**; keep bullets to 1-2 lines.`

const SECTION_GUIDE = `Section order depends on the variant:
- applied_ai: Summary -> Education (compact, 2 entries, no bullets) -> Skills -> Projects (RAG Document Assistant first; then the most relevant of Job Search Agent (agents, LLM evaluation, guardrails) / Medical Image Classifier API / AI Chatbot / DriveShare) -> Experience (the industry role, thesis research, TA only if space).
- mle: Summary -> Education -> Skills -> Experience (the industry role first, then thesis research) -> Projects (RAG and Medical Image Classifier API; Job Search Agent instead of one of them when the posting is about agents, pipelines or automation).
- cv_medical: Summary -> Education -> Skills -> Experience (thesis research first, then the industry role) -> Projects (Medical Image Classifier API, Alzheimer's Classification, DroneX, and Tennis Court Detection or Digital Holographic Microscope if relevant).
Education entries: heading=school, heading_right=location, subheading=degree, subheading_right=dates, no bullets.
Project entries: heading=project name, heading_link=its GitHub URL from the master (empty string if none), heading_right=dates, subheading="" and subheading_right="".`

const FORM_GUIDE = `The brief lists the REAL application form (fetched live). Answer exactly those questions:
- custom_answers: one entry for EVERY free-text (long_text) question on the form, "question" copied exactly from the form label. 80-150 words each unless the question implies shorter; in his voice; company-specific. Adapt the brief's sample answers rather than repeating them. If the form was not available, write 4-6 questions this employer is likely to ask.
- field_answers: one entry for EVERY other question on the form except file uploads, the Education block and EEO/self-identification questions (those come from the standard answers at submit time): names, email, phone, links, location, sponsorship, authorization, start date, salary, how-did-you-hear, selects, yes/no. For selects give the exact option text listed in the brief. Salary per the standard answers. How did you hear: the careers-page / job-board option (or "Other" + "Company careers page").
- "Standing answers Russ gave" in the brief: use those answers exactly (needs_user false). For a select, the form's own spelling of that option.
- Anything under "Questions that go to Russ" -> ONLY a field_answers entry with needs_user: true and answer "" (never a custom_answers entry) - never answer those yourself, not even with a guess. Also list them in notes.
- Any other question, including new kinds: answer it from the standard answers, the verified facts and the master resume in the brief, keeping facts consistent with the brief's earlier answers to the same questions.
  Only if a REQUIRED question needs a plain fact (a date, a school detail, an employer's city) that the brief lacks: follow ${SEARCH} (the only folders you may search, read-only). If an official document states it, use it and append one line to the "## Learned" section at the end of ${KNOWLEDGE} with the exact source path. Otherwise give it a field_answers entry with needs_user: true and answer "", and list it in notes.
- Skills and experience questions follow the "Never sound unable" section of the writing rules: never a bare "No", "I don't have experience" or "I haven't". Name the closest real experience from the master and how it carries over. A Yes/No skills select is "Yes" only when the master shows the skill or a close equivalent he can defend in an interview; otherwise needs_user. Legal and eligibility questions are answered truthfully (a true "No" is fine there).`

const STYLE_RULES = `Writing: the resume summary, the cover letter, every custom answer and every field answer longer than three words follow the brief's writing rules (his own rules, "casual-respectful" style). Exceptions: an option copied exactly from the form, and his standing answers. For a legal or eligibility "No" in a text box write "No." or "No. This would be my first role at {company}." and never "I haven't...". check-app rejects: em dashes, en dashes used as dashes, double dashes, semicolons, emojis, any sentence over 40 words, the banned words ("leverage", "harness" as a verb, "passionate", "showcase", "pivotal", "serves as" ...), "not only X but also Y", "It's not X, it's Y", "From X to Y", and inability wording ("I don't have experience", "unfortunately", "not familiar"). Also: vary sentence length, active voice, no metaphors or cliches, no groups of three unless there really are three items, no dramatic one-line closers, no inflated importance.`

const TRUSTED = `Trusted as given, never flag: answers listed in the brief under "Standing answers Russ gave" (they are his own words), and options copied exactly from the form. Must be verified: a "Yes" to a skills or experience question is a claim; flag it as fabrication unless the master shows that skill or a close, defensible equivalent. An answer that relies on a "## Learned" line of the verified facts: open the source file that line cites and flag the answer if that file does not state the fact, or if the source is in a folder that ${SEARCH} forbids.`

const ONE_READ = brief => `Read ${brief} with ONE Read call (it is the job brief: posting, live form, standing answers, questions for Russ, and the whole candidate context). Read nothing else unless this prompt names it.`

const draftPrompt = (job, date) => `You are writing a job application for Russ, the candidate described in the brief. Today is ${date}.
${ONE_READ(job.brief)}

${RULES}

${STYLE_RULES}

${SECTION_GUIDE}

${FORM_GUIDE}

Write the application with the Write tool to ${appFile(job.key)} as one JSON object in this shape:
${APP_SHAPE}
- key "${job.key}"; resume: the variant named in the brief; mirror the posting's language where the master supports it; the summary names the target role and the single strongest piece of evidence for THIS posting.
- cover_letter: date "${date}" written out like "September 24, 2026", greeting "Dear {Company} Hiring Team,", 3-4 paragraphs, 280-340 words, closing "Sincerely,". Lead with a concrete thing he built that maps to their problem, name the company's actual product or problem from the posting, state his availability and location as the standard answers give them. Where the posting asks for something he has not done, write about the closest thing he has done and how it carries over; never say he lacks it. Always write one, even if the form has no cover-letter field.
Then validate (Bash): ${RUN} check-app ${job.key} --draft
It checks the schema, form coverage, work-authorization rules, the writing rules and the no-fabrication guard. Fix the file and re-run until it prints "ok": true (at most 3 runs). Guard errors mean a number, skill or date is not in the master: remove or correct the claim; never edit the master.
Return the result summary.`

const checkFixPrompt = (job, date) => `You are an adversarial fact-checker and recruiter. Today is ${date}. A drafted job application is at ${appFile(job.key)}.
In ONE turn, read ${job.brief} (the brief: posting, live form and the whole candidate context; the master resume in it is the ONLY source of truth about the candidate) and ${appFile(job.key)} with parallel Read calls. Read nothing else.

Attack the draft, in priority order:
1. FABRICATION: any claim - resume, cover letter, custom answers, field answers - the master does not support. Check every number, tool, date, title, team size, scope word ("production", "at scale", "customers", "led") and outcome, and that each bullet's source_ids contain what the bullet says. Flag every break of the rewording rules.
2. RULE_VIOLATION: field_answers that break the standard answers (work authorization, sponsorship, salary floor/midpoint, start date, location), a work-authorization claim in prose, a "Questions that go to Russ" item answered instead of needs_user, a required form question with no answer, a select answer that is not one of the listed options, a fact found in none of the master, the standard answers or the verified facts, a skills question answered with a bare "No" or inability wording, and any break of the writing rules in the letter or answers (metaphors, cliches, a forced group of three, dramatic one-line closers, inflated importance).
3. OVERCLAIM: technically true but reads as more than it was.
4. KEYWORD_GAP: posting requirements the master DOES support that the draft fails to surface, or a requirement he lacks where adjacent real experience could be named.
5. LENGTH: resume body over ~580 words or cover letter outside 280-340 words.
6. STYLE: filler, repetition between resume and letter, sentence lengths that never vary, machine-sounding prose.
${TRUSTED}
Be harsh - cutting a claim is cheaper than losing credibility in an interview. Do not invent findings for clean sections.

Then FIX the file yourself (Edit or Write): apply every fabrication, rule_violation and overclaim finding - when in doubt cut the claim rather than soften it - and the keyword, length and style findings where they genuinely improve it and stay truthful. Keep the rules: ${RULES}
${STYLE_RULES}
Put your findings in the file's top-level "findings" field: {"findings": [{"severity", "where", "problem", "fix"}], "keyword_coverage": "one sentence", "length_verdict": "one sentence"}.
Validate (Bash): ${RUN} check-app ${job.key}  - fix and re-run until it prints "ok": true (at most 3 runs).
Return the result summary (changes: the findings you fixed, briefly).`

const recheckPrompt = (job) => `You are the last line of defence before a job application is shown to Russ for approval.
In ONE turn, read ${job.brief} (the brief: posting, live form and the whole candidate context) and ${appFile(job.key)} (the FINAL application) with parallel Read calls. Read nothing else.
Report ONLY two kinds of problem - ignore style, length and keywords:
- fabrication/overclaim: any claim in the resume, cover letter, custom_answers or field_answers that neither the master resume nor the verified facts in the brief support, or that breaks one of the rewording rules (check every number, tool, scope word and outcome);
- rule_violation: a work-authorization or residency claim outside the structured authorization fields, an authorization/sponsorship answer that differs from the standard answers, a "Questions that go to Russ" item answered instead of needs_user, a select answer not among the options in the brief, a skills question answered with a bare "No" or with inability wording.
${TRUSTED}
Then write your verdict with the Write tool to ${jobFile(job.key, '.recheck.json')} as {"clean": true|false, "findings": [...]}, and bind it to the file you checked (Bash): ${RUN} stamp-recheck ${job.key}
Return the same verdict (file_written true once written and stamped). Do not modify any other file.`

const refixPrompt = (job, findings) => `A final re-check found problems in the job application ${appFile(job.key)}.
In ONE turn, read ${job.brief} (the brief with the whole candidate context) and ${appFile(job.key)} with parallel Read calls. Fix EXACTLY these findings in the file (cut the claim rather than soften it; change nothing else; new wording follows the writing rules):
${JSON.stringify(findings, null, 1)}
Edit the file in place, then validate (Bash): ${RUN} check-app ${job.key} - fix until it prints "ok": true (at most 3 runs). Return the result summary with key "${job.key}".`

const date = args.date
// keys come from daily.py via an agent: accept only well-formed ones, and build the brief path from the key
const jobs = ((args && args.jobs) || [])
  .map(j => (typeof j === 'string' ? { key: j } : j))
  .filter(j => j && /^[a-z0-9_]+$/.test(j.key))
  .map(j => ({ key: j.key, brief: jobFile(j.key, '.md') }))
if (jobs.length !== ((args && args.jobs) || []).length) log('some job keys were malformed and skipped')
const fail = (job, why) => ({ key: job.key, written: false, check_ok: false, remaining_errors: [why], changes: '', needs_russ: [] })

const results = await pipeline(
  jobs,
  job => run(draftPrompt(job, date), { label: `draft:${job.key}`, phase: 'Draft', schema: RESULT, model: WRITER_MODEL }),
  async (d, job) => {
    if (!d || !d.written) return fail(job, d ? 'draft was not written' : 'draft failed')
    let r = await run(checkFixPrompt(job, date), { label: `check:${job.key}`, phase: 'Check', schema: RESULT })
    if (!r) r = await run(checkFixPrompt(job, date), { label: `check-retry:${job.key}`, phase: 'Check', schema: RESULT })
    if (!r) return fail(job, 'fact-check failed twice')
    if (!r.check_ok) return { ...r, key: job.key }  // mechanical checks still fail: no point spending a re-check on it
    // final re-check of what was actually written; one targeted repair, then it must be clean
    let rc = await run(recheckPrompt(job), { label: `recheck:${job.key}`, phase: 'Recheck', schema: RECHECK })
    if (rc && !rc.clean && rc.findings.length) {
      const r2 = await run(refixPrompt(job, rc.findings), { label: `refix:${job.key}`, phase: 'Recheck', schema: RESULT, model: WRITER_MODEL })
      if (!r2 || !r2.check_ok) return fail(job, 're-check found problems and the repair did not validate')
      rc = await run(recheckPrompt(job), { label: `recheck2:${job.key}`, phase: 'Recheck', schema: RECHECK })
    }
    if (!rc || !rc.clean || !rc.file_written) return { ...r, key: job.key, check_ok: false, remaining_errors: ['final re-check not clean'] }
    return { ...r, key: job.key }
  },
)

const ok = results.filter(r => r && r.check_ok)
log(`${ok.length}/${jobs.length} applications drafted, fact-checked, fixed, re-checked and validated`)
return { results, ok_keys: ok.map(r => r.key) }
