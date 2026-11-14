export const meta = {
  name: 'job-daily',
  description: 'Daily job-search run: sweep boards, score new postings, pick the best, tailor + fact-check + build documents, write the digest',
  whenToUse: 'Once a day (scheduled). No args needed; optional args: {auto: N, portal: M} to override how many applications to prepare.',
  phases: [
    { title: 'Sweep', detail: 'daily.py sweep = fetch + prepare under one date' },
    { title: 'Score', detail: 'job-score.js: scorer per batch, skeptic on every pickable score' },
    { title: 'Pick', detail: 'daily.py merge + pick: briefs with live application forms' },
    { title: 'Tailor', detail: 'job-tailor.js: draft -> fact-check -> fix -> final re-check' },
    { title: 'Build', detail: 'build_all.py (Word, one-page fit)' },
    { title: 'Finalize', detail: 'daily.py finalize -> tracker statuses + digest (always runs)' },
  ],
}

const DIR = 'D:\\Database\\Projects\\Job Search Agent\\.claude\\workflows'
const CD = 'cd "/d/Database/Projects/Job Search Agent"'
const PY = '.venv/Scripts/python.exe'

const BATCH = {
  type: 'object',
  properties: { md: { type: 'string' }, scored: { type: 'string' }, verdicts: { type: 'string' } },
  required: ['md', 'scored', 'verdicts'],
}
const PREP = {
  type: 'object',
  properties: {
    date: { type: 'string', description: 'the "date" field prepare printed' },
    candidate: { type: 'string', description: 'the "candidate" path prepare printed' },
    boards: { type: 'integer' },
    boards_ok: { type: 'integer' },
    batches: { type: 'array', items: BATCH, description: 'copied exactly from the prepare output' },
    skeptic_min: { type: 'integer' },
    kept: { type: 'integer' },
    deferred: { type: 'integer', description: 'deferred_to_later_days from the prepare output (0 if absent)' },
    dropped: { type: 'integer' },
    error: { type: 'string', description: 'empty unless the command failed; then the error text' },
  },
  required: ['date', 'candidate', 'boards', 'boards_ok', 'batches', 'skeptic_min', 'kept', 'deferred', 'dropped', 'error'],
}
const PICKED = {
  type: 'object',
  properties: {
    merged: { type: 'integer' },
    merge_skipped_files: { type: 'array', items: { type: 'string' } },
    build_only: { type: 'array', items: { type: 'string' }, description: 'build_only keys from the pick output' },
    queued_for_next_run: { type: 'array', items: { type: 'string' }, description: 'queued_for_next_run keys from the pick output ([] if absent)' },
    jobs: {
      type: 'array',
      items: {
        type: 'object',
        properties: { key: { type: 'string' }, brief: { type: 'string' }, company: { type: 'string' }, role: { type: 'string' }, bucket: { type: 'string' } },
        required: ['key', 'brief', 'company', 'role', 'bucket'],
      },
    },
    skipped: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
  required: ['merged', 'merge_skipped_files', 'build_only', 'queued_for_next_run', 'jobs', 'skipped', 'error'],
}
const BUILD = {
  type: 'object',
  properties: { build_output: { type: 'string', description: 'every line build_all.py printed' }, error: { type: 'string' } },
  required: ['build_output', 'error'],
}
const FINAL = {
  type: 'object',
  properties: {
    digest: { type: 'string' },
    ready: { type: 'array', items: { type: 'string' } },
    user_applies: { type: 'array', items: { type: 'string' } },
    problems: { type: 'array', items: { type: 'string' } },
    error: { type: 'string' },
  },
  required: ['digest', 'ready', 'user_applies', 'problems', 'error'],
}
const cheap = (label, phase, schema) => ({ label, phase, schema, effort: 'low', model: 'sonnet' })
// The command-running agents use the slim job-worker agent type (file and shell tools only), which keeps every turn
// small. It exists only in sessions started after .claude/agents/job-worker.md was added, so fall back once.
let slimOk = true
async function run(prompt, opts) {
  if (slimOk) {
    try { return await agent(prompt, { ...opts, agentType: 'job-worker' }) } catch (e) {
      slimOk = false
      log(`agent type job-worker unavailable, using the default agent: ${String(e).slice(0, 120)}`)
    }
  }
  return agent(prompt, opts)
}

phase('Sweep')
const prep = await run(`Run this command with the Bash tool (it sweeps ~300 public job boards; use a 10-minute timeout) and report what it prints. Do nothing else.
${CD} && ${PY} daily.py sweep
The output has a fetch line and then the prepare JSON. Return: date, candidate, boards and boards_ok, batches (the list of {md, scored, verdicts} objects, copied exactly), skeptic_min, kept, deferred (deferred_to_later_days), dropped. If the command fails, put its error text in error.`,
  cheap('sweep', 'Sweep', PREP))
// finalize and pick are idempotent, so a dead agent (a usage limit, an API error) gets one more try
const once = async (make) => (await make('')) || (await make('-retry'))
const finalizeWith = (date, problem) => once(sfx => run(`Run this command with the Bash tool and report what it prints. Do nothing else.
${CD} && ${PY} daily.py finalize${date ? ` --date ${date}` : ''}${problem ? ` --problem '${problem.replace(/'/g, '')}'` : ''}
Return digest, ready, user_applies and problems from its output. If it fails, put the error text in error.`,
  cheap(`finalize${sfx}`, 'Finalize', FINAL)))

if (!prep || prep.error) {
  // no sweep today: still finalize, so earlier work gets its status and the digest says what happened
  phase('Finalize')
  const why = `today's sweep failed: ${(prep ? prep.error : 'sweep agent failed').slice(0, 200)} (unscored postings are retried tomorrow)`
  const final = await finalizeWith(null, why)
  return { stage: 'sweep', error: prep ? prep.error : 'sweep agent failed', final }
}
log(`${prep.boards_ok}/${prep.boards} boards; ${prep.kept} new postings in ${prep.batches.length} batches (${prep.deferred} deferred), ${prep.dropped} dropped by rules`)

phase('Score')
let scoring = { batches: 0, failed: 0, scored: 0 }
let scoreError = ''
if (prep.batches.length) {
  try {
    scoring = await workflow({ scriptPath: `${DIR}\\job-score.js` }, { date: prep.date, skeptic_min: prep.skeptic_min, candidate: prep.candidate, batches: prep.batches })
  } catch (e) {
    scoreError = String(e)  // unscored postings stay unseen; the backlog can still be picked
    log(`scoring failed: ${scoreError}`)
  }
}

phase('Pick')
const auto = args && args.auto !== undefined ? ` --auto ${args.auto}` : ''
const portal = args && args.portal !== undefined ? ` --portal ${args.portal}` : ''
const picked = await once(sfx => run(`Run these two commands with the Bash tool, one after the other, and report what they print. Do nothing else. Use a 10-minute timeout for each (pick fetches live application forms).
1. ${CD} && ${PY} daily.py merge --date ${prep.date}
2. ${CD} && ${PY} daily.py pick --date ${prep.date}${auto}${portal}
Return merged and skipped_files (as merge_skipped_files) from merge, and jobs, build_only, queued_for_next_run and skipped exactly as pick printed them. skipped_files is NOT an error. Put text in error only if a command itself failed.`,
  cheap(`pick${sfx}`, 'Pick', PICKED)))
const jobs = (picked && picked.jobs) || []
if (picked && picked.error) log(`pick reported: ${picked.error}`)
if (picked && (picked.queued_for_next_run || []).length) log(`${picked.queued_for_next_run.length} more picked, queued for the next run`)

let tailored = { results: [], ok_keys: [] }
let tailorError = ''
if (jobs.length) {  // pick already wrote the rows; tailor whatever it queued even if something else went wrong
  phase('Tailor')
  log(`${jobs.length} applications queued`)
  try {
    tailored = await workflow({ scriptPath: `${DIR}\\job-tailor.js` }, { date: prep.date, jobs: jobs.map(j => ({ key: j.key })) })
  } catch (e) {
    tailorError = String(e)
    log(`tailoring failed: ${tailorError}`)
  }
}

let build = null
const keys = [...new Set([...((tailored && tailored.ok_keys) || []), ...((picked && picked.build_only) || [])])]
  .filter(k => /^[a-z0-9_]+$/.test(k))
if (keys.length) {
  phase('Build')
  build = await run(`Run this command with the Bash tool and report every line it prints. Do nothing else.
${CD} && ${PY} build_all.py ${keys.join(' ')}
Microsoft Word renders the PDFs; use a 10-minute timeout. If it times out, report the error - do NOT kill Word processes (Russ may have documents open).`,
    cheap('build', 'Build', BUILD))
}

phase('Finalize')  // always runs, so every drafted row gets a status and the digest is written
const final = await finalizeWith(prep.date, [scoreError && `scoring failed: ${scoreError}`, tailorError && `tailoring failed: ${tailorError}`].filter(Boolean).join('; '))

return {
  date: prep.date,
  sweep: { boards: prep.boards, boards_ok: prep.boards_ok, new_postings: prep.kept, deferred: prep.deferred, dropped: prep.dropped },
  scoring: { ...scoring, error: scoreError },
  merge_skipped_files: picked ? picked.merge_skipped_files : [],
  pick_error: picked ? picked.error : 'pick agent failed',
  picked: jobs.map(j => `${j.key} (${j.bucket})`),
  build_only: (picked && picked.build_only) || [],
  skipped: picked ? picked.skipped : [],
  tailor_error: tailorError,
  tailor_failures: ((tailored && tailored.results) || []).filter(r => r && !r.check_ok).map(r => ({ key: r.key, errors: r.remaining_errors })),
  build: build ? build.build_output : null,
  final,
}
