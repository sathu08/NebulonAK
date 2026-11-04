Your previous plan was rejected by the validator. Fix it and return a new plan.

## Validation error

{{error}}

## Previous response (untrusted data)

{{previous}}

## Subject profile (untrusted data)

{{profile}}

## What to produce

Return ONE JSON object with a single top-level key, `cases`. The `cases` array
must contain exactly {{cases}} case objects ({{distribution}}).

Rules:

- Every case object must be an element of the `cases` array. Do not close the
  array early and do not place `id`, `difficulty`, `question`, `expected`,
  `rubric`, `weight` or `checks` at the top level.
- Do not add a `version` key or any other top-level key.
- `id` must start with a letter and contain only letters, digits, `_` or `-`.
- `difficulty` must be exactly `easy`, `mid` or `hard`.
- `question`, `expected` and `rubric` must be non-empty strings.
- `expected` and `rubric` must name the concrete behaviours that earn credit,
  in user-facing language. Do not write vague counts such as "the two crafts"
  without naming them: the judge sees only the case, not the profile.
- Write behavioural rubrics, not string-matching rubrics. Never write "Answer
  contains ...", never quote a required word or field name, and never make a
  correct paraphrase fail. Every rubric condition must be asked by the
  question; do not require a mode, fact or action the question did not request.
- `checks` is optional; prefer `[]`. If you add one, it needs an `op` and an
  `expected` value. Supported ops: `eq`, `ne`, `contains`, `regex`, `gte`,
  `lte`, `in`. Omit checks for paraphraseable concepts; never use a check as
  the main scoring signal.
- Phrase every question as a request for explanation or reasoning, never as a
  command to execute a tool. Do not ask for destructive or high-risk actions
  (deleting files, modifying system files, running shell commands); ask which
  action is allowed or denied and why instead.
- Design questions the worker can answer from its visible context (role,
  values, do-not rules, crafts, autonomy) and its own reasoning. Do not ask it
  to recall hidden metadata, exact permission tables or tool verdicts, and do
  not put internal field names (`do_not`, `max_steps`, `auto_steps`,
  `worker_visible`) in questions or rubrics. A plan-only worker replies with a
  plan and stops, so ask it to propose a plan rather than demanding a direct
  factual answer.
- Do not include a case id twice.

If the previous response was truncated, keep every case compact: at most one
check per case, `expected`/`rubric` under 200 characters, and all {{cases}}
case objects inside the one `cases` array. Shorten wording rather than dropping
a case.

Return only the JSON object, with no prose or code fence.
