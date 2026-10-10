export const meta = {
  name: 'scoped-fanout',
  description: 'Fan out scoped tasks with an inline brief, per-task file allowlists, capped outputs and a token budget',
  whenToUse: 'Audits, migrations and broad sweeps only. Never planning, interviews, specs or ticket breakdowns (docs/mvp1/model-policy.md, fan-out contract).',
  phases: [
    { title: 'Run', detail: 'one agent per task, reading only its allowlisted files' },
    { title: 'Verify', detail: 'one skeptic per decision-changing claim, only when asked' },
  ],
}

// Enforces rules 2-4 and 6 of the fan-out contract in docs/mvp1/model-policy.md:
//   2. the brief is distilled text passed inline (never a path to a research dump);
//   3. every task names the only files it may read;
//   4. every answer is length-capped by its schema;
//   6. a token budget is stated up front and checked before each agent starts.
//
// args = {
//   brief:  string, <= 12000 chars (distilled facts + decisions, e.g. a decision log's Facts section)
//   budget: number, output tokens this run may spend (checked against budget.spent())
//   tasks:  [{ key, prompt, files: [path or path:lines, ...], maxChars?: number (default 6000, max 12000) }]
//   verify?: [{ claim, files: [...] }]   // only claims that would change a decision
// }

const MAX_BRIEF = 12000
const DEFAULT_ANSWER = 6000
const MAX_ANSWER = 12000

function fail(rule, message) {
  throw new Error(`scoped-fanout: ${message} (fan-out contract rule ${rule})`)
}

if (!args || typeof args !== 'object') fail(2, 'pass args {brief, budget, tasks}')
if (typeof args.brief !== 'string' || !args.brief.trim()) fail(2, 'brief must be inline text')
if (args.brief.length > MAX_BRIEF) fail(2, `brief is ${args.brief.length} chars; distil it to <= ${MAX_BRIEF}`)
if (!Number.isFinite(args.budget) || args.budget <= 0) fail(6, 'state a positive token budget')
if (!Array.isArray(args.tasks) || args.tasks.length === 0) fail(3, 'pass at least one task')

const tasks = args.tasks.map((t, i) => {
  if (!t || typeof t.key !== 'string' || typeof t.prompt !== 'string') fail(3, `task ${i} needs key and prompt`)
  if (!Array.isArray(t.files) || t.files.length === 0) fail(3, `task ${t.key} needs a non-empty files allowlist`)
  const maxChars = t.maxChars === undefined ? DEFAULT_ANSWER : t.maxChars
  if (!Number.isFinite(maxChars) || maxChars <= 0 || maxChars > MAX_ANSWER) fail(4, `task ${t.key} maxChars must be 1..${MAX_ANSWER}`)
  return { key: t.key, prompt: t.prompt, files: t.files, maxChars }
})
const claims = Array.isArray(args.verify) ? args.verify : []
claims.forEach((c, i) => {
  if (!c || typeof c.claim !== 'string' || !Array.isArray(c.files) || c.files.length === 0) fail(5, `verify ${i} needs claim and files`)
})

const answerSchema = maxChars => ({
  type: 'object',
  properties: {
    key: { type: 'string' },
    answer: { type: 'string', maxLength: maxChars },
    needs: { type: 'array', items: { type: 'string' }, description: 'files or facts outside the allowlist you would need; do not read them' },
  },
  required: ['key', 'answer', 'needs'],
})

const verdictSchema = {
  type: 'object',
  properties: {
    claim: { type: 'string' },
    holds: { type: 'boolean' },
    evidence: { type: 'string', maxLength: 2000 },
  },
  required: ['claim', 'holds', 'evidence'],
}

const scope = files =>
  `Read ONLY these files, nothing else in the repo and no research dumps: ${files.join(', ')}. ` +
  'If you need anything outside them, list it under "needs" instead of reading it.'

const skipped = []
function overBudget(label) {
  if (budget.spent() < args.budget) return false
  skipped.push(label)
  log(`budget ${args.budget} reached; skipped ${label}`)
  return true
}

phase('Run')
const results = await parallel(tasks.map(t => () => {
  if (overBudget(t.key)) return null
  return agent(
    `${args.brief}\n\nTASK ${t.key}: ${t.prompt}\n\n${scope(t.files)}\nAnswer in at most ${t.maxChars} characters. Return key="${t.key}".`,
    { label: t.key, phase: 'Run', schema: answerSchema(t.maxChars) },
  )
}))

phase('Verify')
const verdicts = await parallel(claims.map((c, i) => () => {
  if (overBudget(`verify-${i}`)) return null
  return agent(
    `${args.brief}\n\nTry to REFUTE this claim; if you cannot confirm it, holds=false.\nCLAIM: ${c.claim}\n\n${scope(c.files)}`,
    { label: `verify-${i}`, phase: 'Verify', schema: verdictSchema },
  )
}))

return { results: results.filter(Boolean), verdicts: verdicts.filter(Boolean), skipped, spent: budget.spent() }
