export const meta = {
  name: 'job-tailor',
  description: 'Draft, fact-check and fix a tailored resume, cover letter and form answers for each queued job',
  whenToUse: 'After `daily.py pick` or `daily.py add`. args: {date: "YYYY-MM-DD", jobs: [{key, brief}]}',
  phases: [
    { title: 'Draft', detail: 'one drafter per job: resume JSON, cover letter, answers for the real form' },
    { title: 'Check', detail: 'adversarial fact-check against the master resume + answer rules' },
    { title: 'Fix', detail: 'apply every finding, write private/applications/<key>.json, pass daily.py check-app' },
    { title: 'Recheck', detail: 'fresh fact-check of the written file (session model); one targeted repair, then it must be clean' },
  ],
}

const ROOT = 'D:\\Database\\Projects\\Job Search Agent'
const RUN = 'cd "/d/Database/Projects/Job Search Agent" && .venv/Scripts/python.exe daily.py'
const MASTER = `${ROOT}\\private\\resume_master.json`
const ANSWERS = `${ROOT}\\private\\application_answers.md`
const BANK = `${ROOT}\\private\\answer_bank.md`
const TRAPS = `${ROOT}\\private\\tailoring_rules.md`
const WRITING = `${ROOT}\\private\\writing_rules.md`
const KNOWLEDGE = `${ROOT}\\private\\knowledge.md`
const QBANK = `${ROOT}\\private\\question_bank.json`
const SEARCH = `${ROOT}\\private\\search_rules.md`
// Usage: drafting, the first review and fixing run on Sonnet. The final re-check, which gates what Russ sees, keeps
// the session model. check-app covers everything mechanical (schema, form coverage, select options, writing rules).
const WRITER_MODEL = 'sonnet'
const CHECK_MODEL = 'sonnet'
// Jobs tailored at once. Small groups finish completely before the next starts, so a run that hits the usage limit
// keeps its finished applications instead of leaving every job half done; a same-day re-run continues with the rest.
const TAILOR_PARALLEL = 2
const ONE_TURN = 'Read them all in ONE turn with parallel Read calls: every extra turn re-sends everything read so far.'

const RESUME = {
  type: 'object',
  properties: {
    variant: { type: 'string', enum: ['applied_ai', 'mle', 'cv_medical'] },
    summary: { type: 'string', description: '2-3 sentences, no first person, **bold** at most 2 key phrases' },
    skills: {
      type: 'array',
      items: {
        type: 'object',
        properties: { label: { type: 'string' }, items: { type: 'array', items: { type: 'string' } } },
        required: ['label', 'items'],
      },
      description: '3-5 groups, most relevant group first; every item must appear in the master skills',
    },
    sections: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          title: { type: 'string' },
          entries: {
            type: 'array',
            items: {
              type: 'object',
              properties: {
                heading: { type: 'string' },
                heading_link: { type: 'string', description: 'GitHub URL for projects, else empty string' },
                link_label: { type: 'string', description: 'usually "GitHub"; empty when no link' },
                heading_right: { type: 'string', description: 'location for jobs, dates for projects - must match master exactly' },
                subheading: { type: 'string' },
                subheading_right: { type: 'string', description: 'dates - must match master exactly' },
                bullets: {
                  type: 'array',
                  items: {
                    type: 'object',
                    properties: {
                      text: { type: 'string' },
                      source_ids: { type: 'array', items: { type: 'string' }, description: 'ids of master bullets this is derived from' },
                    },
                    required: ['text', 'source_ids'],
                  },
                },
              },
              required: ['heading', 'heading_link', 'link_label', 'heading_right', 'subheading', 'subheading_right', 'bullets'],
            },
          },
        },
        required: ['title', 'entries'],
      },
    },
  },
  required: ['variant', 'summary', 'skills', 'sections'],
}

const LETTER = {
  type: 'object',
  properties: {
    date: { type: 'string' },
    company: { type: 'string' },
    role: { type: 'string' },
    greeting: { type: 'string' },
    paragraphs: { type: 'array', items: { type: 'string' }, description: '3-4 paragraphs, 280-340 words total' },
    closing: { type: 'string' },
  },
  required: ['date', 'company', 'role', 'greeting', 'paragraphs', 'closing'],
}

const QA = {
  type: 'array',
  items: {
    type: 'object',
    properties: { question: { type: 'string' }, answer: { type: 'string' } },
    required: ['question', 'answer'],
  },
}

const FIELDS = {
  type: 'array',
  items: {
    type: 'object',
    properties: {
      question: { type: 'string', description: 'the form label, copied exactly' },
      answer: { type: 'string', description: 'for selects: the exact option text to choose; empty when needs_user' },
      needs_user: { type: 'boolean', description: 'true for questions listed under "Questions that go to Russ"' },
    },
    required: ['question', 'answer', 'needs_user'],
  },
}

const DRAFT_OUT = {
  type: 'object',
  properties: { resume: RESUME, cover_letter: LETTER, custom_answers: QA, field_answers: FIELDS, notes: { type: 'string' } },
  required: ['resume', 'cover_letter', 'custom_answers', 'field_answers', 'notes'],
}

const FINDINGS = {
  type: 'object',
  properties: {
    findings: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          severity: { type: 'string', enum: ['fabrication', 'overclaim', 'rule_violation', 'keyword_gap', 'style', 'length'] },
          where: { type: 'string', description: 'e.g. "summary", "RevSolz bullet 2", "cover letter para 3", "field: sponsorship"' },
          problem: { type: 'string' },
          fix: { type: 'string', description: 'concrete replacement wording or instruction' },
        },
        required: ['severity', 'where', 'problem', 'fix'],
      },
    },
    keyword_coverage: { type: 'string' },
    length_verdict: { type: 'string' },
  },
  required: ['findings', 'keyword_coverage', 'length_verdict'],
}

const RESULT = {
  type: 'object',
  properties: {
    key: { type: 'string' },
    written: { type: 'boolean' },
    check_ok: { type: 'boolean', description: 'true only if `daily.py check-app` printed "ok": true on the final file' },
    remaining_errors: { type: 'array', items: { type: 'string' } },
    changes: { type: 'string', description: 'what the fix pass changed, briefly' },
    needs_russ: { type: 'array', items: { type: 'string' }, description: 'form questions left for Russ' },
  },
  required: ['key', 'written', 'check_ok', 'remaining_errors', 'changes', 'needs_russ'],
}

const RULES = `THE ONE RULE: every claim must be traceable to ${MASTER}. You may select, reorder, shorten, merge and reword its facts, and use plain connective language. You may NOT add an employer, title, date, metric, tool, technology, scope or outcome that is not in the master file. If the posting wants something the candidate lacks, leave it out - do not soften it into a half-claim.
Read ${TRAPS} first: it lists the rewording mistakes a model tends to make with this master file (stating a target as a result, dropping a qualifier, calling a design shipped, inflating a role's scope) and the name rules.
Work authorization is answered ONLY in structured fields, exactly as ${ANSWERS} says. Never write a citizenship or residency status ("citizen", "permanent resident", "green card holder") anywhere, and never put work-authorization claims in the cover letter or free-text answers.
Every resume bullet must carry source_ids listing the master bullet ids it came from. Contact details are injected later - do not output them.
Length: the resume must fit ONE page - roughly 480-580 words of body text. 3-4 bullets for the most relevant entries, 2 for the rest; drop entries that do not serve this posting. Wrap at most two key phrases per bullet in **double asterisks**; keep bullets to 1-2 lines.`

const SECTION_GUIDE = `Section order depends on the variant:
- applied_ai: Summary -> Education (compact, 2 entries, no bullets) -> Skills -> Projects (RAG Document Assistant first; then the most relevant of Job Search Agent (agents, LLM evaluation, guardrails) / Medical Image Classifier API / AI Chatbot / DriveShare) -> Experience (RevSolz, thesis research, TA only if space).
- mle: Summary -> Education -> Skills -> Experience (RevSolz first, then thesis research) -> Projects (RAG and Medical Image Classifier API; Job Search Agent instead of one of them when the posting is about agents, pipelines or automation).
- cv_medical: Summary -> Education -> Skills -> Experience (thesis research first, then RevSolz) -> Projects (Medical Image Classifier API, Alzheimer's Classification, DroneX, and Tennis Court Detection or Digital Holographic Microscope if relevant).
Education entries: heading=school, heading_right=location, subheading=degree, subheading_right=dates, no bullets.
Project entries: heading=project name, heading_link=its GitHub URL from the master (empty string if none), heading_right=dates, subheading="" and subheading_right="".`

const FORM_GUIDE = (key) => `The job brief lists the REAL application form (fetched live) with its options. Open ${ROOT}\\data\\jobs\\${key}.form.json only when the brief says an option list was cut ("+N more"). Answer exactly those questions:
- custom_answers: one entry for EVERY free-text (long_text) question on the form, "question" copied exactly from the form label. 80-150 words each unless the question implies shorter; in his voice; company-specific. Adapt ${BANK} rather than repeating it verbatim. If the form was not available, write 4-6 questions this employer is likely to ask.
- field_answers: one entry for EVERY other question on the form except file uploads, the Education block and EEO/self-identification questions (gender, race, veteran, disability, orientation - those come from ${ANSWERS} at submit time): names, email, phone, links, location, sponsorship, authorization, start date, salary, how-did-you-hear, selects, yes/no. For selects give the exact option text (check-app rejects anything else). Salary per ${ANSWERS}. How did you hear: the careers-page / job-board option (or "Other" + "Company careers page").
- "Standing answers Russ gave" in the brief: use those answers exactly (needs_user false). For a select, the form's own spelling of that option.
- Anything under "Questions that go to Russ" -> ONLY a field_answers entry with needs_user: true and answer "" (never a custom_answers entry, even for long text) - never answer those yourself, not even with a guess. Also list them in notes.
- Any other question, including new kinds you have not seen: answer it from ${ANSWERS}, ${KNOWLEDGE} (verified facts: education dates, GPA, citizenship, years of experience, current situation) and ${MASTER}. ${QBANK} (if it exists) holds answers already given on earlier forms: keep facts consistent with them, but write text fresh for this company.
  If a plain fact is still missing (a date, a school detail, an employer's city), follow ${SEARCH}: it names the only folders you may search (read-only) and the ones you must never use. Skills, tools, metrics and experience claims NEVER come from a search: only from ${MASTER}. If you find the fact in an official document, use it and APPEND one line to the "## Learned" section at the end of ${KNOWLEDGE}: the fact and the exact source file path. If you cannot find it, give it a field_answers entry with needs_user: true and answer "", and list it in notes (Russ is asked once; his answer is reused for every later form).
- Skills and experience questions follow the "Never sound unable" section of ${WRITING}: never a bare "No", "I don't have experience" or "I haven't". Name the closest real experience from the master file and how it carries over. A Yes/No skills select is "Yes" only when the master shows the skill or a close equivalent he can defend in an interview; otherwise needs_user. Legal and eligibility questions are answered truthfully (a true "No" is fine there).`

const STYLE_RULES = `Writing: the resume summary, the cover letter, every custom answer and every field answer longer than three words follow ${WRITING} (his own rules, "casual-respectful" style). Exceptions: an option copied exactly from the form, and his standing answers. For a legal or eligibility "No" in a text box write "No." or "No. This would be my first role at {company}." and never "I haven't...". Mechanical rules that daily.py check-app rejects: no em dashes, no en dashes used as dashes, no double dashes, no semicolons, no emojis, no sentence over 40 words, none of the banned words ("leverage", "harness" as a verb, "passionate", "showcase", "pivotal", "serves as" ...), no "not only X but also Y", no "It's not X, it's Y", no "From X to Y", no inability wording ("I don't have experience", "unfortunately", "not familiar"). Also: vary sentence length, active voice, no metaphors or cliches, no groups of three unless there really are three items, no dramatic one-line closers, no inflated importance. Check a paragraph with: cd "/d/Database/Projects/Job Search Agent" && .venv/Scripts/python.exe style_guard.py "<text>"`

const TRUSTED = `Trusted as given, never flag: answers listed in the brief under "Standing answers Russ gave" (they are his own words; report a conflict with ${KNOWLEDGE} in notes only), and options copied exactly from the form. Must be verified: a "Yes" to a skills or experience question is a claim; flag it as fabrication unless ${MASTER} shows that skill or a close, defensible equivalent. Any answer that relies on a line in the "## Learned" section of ${KNOWLEDGE}: open the source file that line cites and flag the answer as fabrication if that file does not state the fact, or if the source is in a folder that ${SEARCH} forbids.`

const draftPrompt = (job, date) => `You are writing a job application for Russ, the candidate in ${MASTER} (his legal and display names are in ${ANSWERS}). Today is ${date}.
Read these files (${ONE_TURN}):
1. ${MASTER} - the ONLY source of facts about him, and ${TRAPS} - the rewording rules and the name rules
2. ${job.brief} - the job brief: posting text, screener notes, gaps, keywords, the resume variant to use, and the live application form
3. ${ANSWERS} - standard answers (availability, work authorization, location, salary rules, EEO)
4. ${KNOWLEDGE} - verified facts for other form questions (education dates, GPA, citizenship, years of experience)
5. ${WRITING} - his writing rules: every sentence you write in his name follows them
6. ${BANK} - prepared answers for common questions (adapt, never copy)

${RULES}

${STYLE_RULES}

${SECTION_GUIDE}

${FORM_GUIDE(job.key)}

Produce:
1. resume - the tailored resume using the variant named in the brief. Mirror the posting's language where the master supports it. The summary names the target role and the single strongest piece of evidence for THIS posting.
2. cover_letter - date "${date}" written out like "September 24, 2026", greeting "Dear {Company} Hiring Team,", 3-4 paragraphs, 280-340 words, closing "Sincerely,". Lead with a concrete thing he built that maps to their problem, name the company's actual product or problem from the posting, state his availability and location as ${ANSWERS} gives them. Follow ${WRITING}. Where the posting asks for something he has not done, write about the closest thing he has done and how it carries over; never say he lacks it. Always write one, even if the form has no cover-letter field.
3. custom_answers and field_answers as described above.
notes: anything the human should check before submitting.`

const checkPrompt = (job, draft) => `You are an adversarial fact-checker and recruiter reviewing a drafted job application.
Read ${MASTER} (the only source of truth about the candidate), ${TRAPS} (the rewording rules), ${ANSWERS} (the rules for form answers), ${KNOWLEDGE} (verified facts for other form questions), ${WRITING} (his writing rules) and ${job.brief} (the posting, screener notes and live form). ${ONE_TURN}
daily.py check-app already enforces the schema, form coverage, select options, work-authorization fields and the mechanical writing rules: spend your effort on what a regex cannot see.

Draft to attack:
${JSON.stringify(draft)}

In priority order:
1. FABRICATION: any claim - resume, cover letter, custom answers - the master file does not support. Check every number, tool, date, title, team size, scope word ("production", "at scale", "customers", "led") and outcome, and that each bullet's source_ids contain what the bullet says. Flag every break of the rewording rules in ${TRAPS}.
2. RULE_VIOLATION: field_answers that break ${ANSWERS} (work authorization, sponsorship, salary floor/midpoint, start date, location), a work-authorization claim in prose, a question from "Questions that go to Russ" answered instead of marked needs_user, a fact used in an answer that is in none of ${MASTER}, ${ANSWERS} or ${KNOWLEDGE}, a skills question answered with a bare "No" or inability wording ("I don't have", "unfortunately", "not familiar"), and any break of ${WRITING} in the cover letter or answers (dashes, semicolons, banned words, "not only X but also Y", "It's not X, it's Y", "From X to Y", metaphors, cliches, a group of three that is not really three items, dramatic one-line closers, fragments for effect, inflated importance).
3. OVERCLAIM: technically true but reads as more than it was.
4. KEYWORD_GAP: posting requirements the master DOES support but the draft failed to surface. Also a posting requirement he lacks where the draft could name adjacent real experience and does not.
5. LENGTH: resume body over ~580 words or cover letter outside 280-340 words; say exactly what to cut.
6. STYLE: filler, repetition between resume and letter, sentence lengths that never vary, machine-sounding prose.
${TRUSTED}
Give a concrete fix for every finding. Be harsh - cutting a claim is cheaper than losing credibility in an interview. Do not invent findings for clean sections.`

const fixPrompt = (job, draft, findings, date) => `You are finalising a job application. Today is ${date}.
Read ${MASTER} (the only source of facts), ${TRAPS}, ${ANSWERS} (form-answer rules), ${KNOWLEDGE} (verified facts), ${WRITING} (his writing rules) and ${job.brief} (posting and live form). ${ONE_TURN}

DRAFT:
${JSON.stringify(draft)}

FACT-CHECKER FINDINGS:
${JSON.stringify(findings)}

Apply every fabrication, rule_violation and overclaim finding - mandatory; when in doubt cut the claim rather than soften it. Apply keyword, length and style findings where they genuinely improve the application and stay truthful. Ignore any finding that would itself introduce an unsupported claim.
${RULES}
${STYLE_RULES}
${SECTION_GUIDE}
${FORM_GUIDE(job.key)}

Then WRITE the final application with the Write tool to ${ROOT}\\private\\applications\\${job.key}.json as one JSON object:
{"key": "${job.key}", "company", "role", "url" (from the brief), "resume", "cover_letter", "custom_answers", "field_answers", "notes", "findings": <the fact-checker findings object above, verbatim>, "fixed_applied": true}
Validate it (Bash): ${RUN} check-app ${job.key}
It runs the no-fabrication guard, the schema, form-coverage and word-count checks. Fix the file and re-run until it prints "ok": true (at most 4 attempts). Guard errors mean a number, skill or date is not in the master - remove or correct the claim; never edit the master file.
Return the result summary.`

const RECHECK = {
  type: 'object',
  properties: {
    clean: { type: 'boolean', description: 'true only if there is no fabrication and no rule violation anywhere in the file' },
    findings: FINDINGS.properties.findings,
    file_written: { type: 'boolean' },
  },
  required: ['clean', 'findings', 'file_written'],
}

const appFile = key => `${ROOT}\\private\\applications\\${key}.json`
const jobFile = (key, ext) => `${ROOT}\\data\\jobs\\${key}${ext}`

const recheckPrompt = (job) => `You are the last line of defence before a job application is shown to Russ for approval.
Read ${MASTER} (the only source of facts about him), ${TRAPS}, ${ANSWERS}, ${KNOWLEDGE}, ${job.brief} and the FINAL application ${appFile(job.key)}. ${ONE_TURN} (check-app has already verified select options, form coverage and the mechanical rules.)
Report ONLY two kinds of problem - ignore style, length and keywords:
- fabrication/overclaim: any claim in the resume, cover letter, custom_answers or field_answers that neither the master file nor ${KNOWLEDGE} supports, or that breaks one of the rewording rules in ${TRAPS} (check every number, tool, scope word and outcome);
- rule_violation: a work-authorization or residency claim outside the structured authorization fields, an authorization/sponsorship answer that differs from ${ANSWERS}, a "Questions that go to Russ" item answered instead of needs_user, a skills question answered with a bare "No" or with inability wording.
${TRUSTED}
Then write your verdict with the Write tool to ${jobFile(job.key, '.recheck.json')} as {"clean": true|false, "findings": [...]}, and bind it to the file you checked by running (Bash): ${RUN} stamp-recheck ${job.key}
Return the same verdict (file_written true once written and stamped). Do not modify any other file.`

const refixPrompt = (job, findings) => `A final re-check found problems in the job application ${appFile(job.key)}.
Read ${MASTER}, ${TRAPS}, ${ANSWERS}, ${KNOWLEDGE}, ${WRITING} and ${job.brief}. ${ONE_TURN} Fix EXACTLY these findings in the file (cut the claim rather than soften it; change nothing else; new wording follows ${WRITING}):
${JSON.stringify(findings)}
Edit the file in place, then validate: ${RUN} check-app ${job.key} - fix until it prints "ok": true (at most 3 attempts). Return the result summary with key "${job.key}".`

const date = args.date
// keys come from daily.py via an agent: accept only well-formed ones, and build the brief path from the key
const jobs = ((args && args.jobs) || [])
  .map(j => (typeof j === 'string' ? { key: j } : j))
  .filter(j => j && /^[a-z0-9_]+$/.test(j.key))
  .map(j => ({ key: j.key, brief: jobFile(j.key, '.md') }))
if (jobs.length !== ((args && args.jobs) || []).length) log('some job keys were malformed and skipped')
const fail = (job, why) => ({ key: job.key, written: false, check_ok: false, remaining_errors: [why], changes: '', needs_russ: [] })

const tailorGroup = group => pipeline(
  group,
  job => agent(draftPrompt(job, date), { label: `draft:${job.key}`, phase: 'Draft', schema: DRAFT_OUT, model: WRITER_MODEL }),
  async (draft, job) => {
    if (!draft) return null
    let f = await agent(checkPrompt(job, draft), { label: `check:${job.key}`, phase: 'Check', schema: FINDINGS, model: CHECK_MODEL })
    if (!f) f = await agent(checkPrompt(job, draft), { label: `check-retry:${job.key}`, phase: 'Check', schema: FINDINGS, model: CHECK_MODEL })
    return f ? { draft, findings: f } : { failed: 'fact-check failed twice; not written' }
  },
  async (prev, job) => {
    if (!prev) return fail(job, 'draft failed')
    if (prev.failed) return fail(job, prev.failed)
    const r = await agent(fixPrompt(job, prev.draft, prev.findings, date), { label: `fix:${job.key}`, phase: 'Fix', schema: RESULT, model: WRITER_MODEL })
    if (!r) return fail(job, 'fix stage failed')
    if (!r.written) return { ...r, key: job.key, check_ok: false, remaining_errors: [...(r.remaining_errors || []), 'fix stage did not write the file'] }
    if (!r.check_ok) return { ...r, key: job.key }  // mechanical checks failed: no point spending a re-check on it
    // final re-check of what was actually written; one targeted repair, then it must be clean
    let rc = await agent(recheckPrompt(job), { label: `recheck:${job.key}`, phase: 'Recheck', schema: RECHECK })
    if (rc && !rc.clean && rc.findings.length) {
      const r2 = await agent(refixPrompt(job, rc.findings), { label: `refix:${job.key}`, phase: 'Recheck', schema: RESULT, model: WRITER_MODEL })
      if (!r2 || !r2.check_ok) return fail(job, 're-check found problems and the repair did not validate')
      rc = await agent(recheckPrompt(job), { label: `recheck2:${job.key}`, phase: 'Recheck', schema: RECHECK })
    }
    if (!rc || !rc.clean || !rc.file_written) return { ...r, key: job.key, check_ok: false, remaining_errors: ['final re-check not clean'] }
    return { ...r, key: job.key }
  },
)

const results = []
for (let i = 0; i < jobs.length; i += TAILOR_PARALLEL) {
  results.push(...await tailorGroup(jobs.slice(i, i + TAILOR_PARALLEL)))
}

const ok = results.filter(r => r && r.check_ok)
log(`${ok.length}/${jobs.length} applications drafted, fact-checked, fixed, re-checked and validated`)
return { results, ok_keys: ok.map(r => r.key) }
