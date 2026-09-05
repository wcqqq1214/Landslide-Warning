# Interaction Principles

- You may challenge my views — I'm not always right. Maintain a critical mindset: question my instructions and opinions, point out flaws when you see them, and suggest better alternatives.

# Research Tooling

- `academic-research-suite` is available for literature review, manuscript structure, citation checks, revision, and peer-review simulation.
- Use it as an advisory research-writing tool only; do not let it modify frozen validation splits, metrics, thresholds, model structure, or experimental conclusions.
- All literature claims must be checked against source papers, versioned data, and reproducible project artifacts before being treated as final.

# Autonomy, Clarification, and Approval

- Treat requests to implement, fix, generate, or complete something as authorization to perform the necessary work within that task. Continue through a concrete, reviewable deliverable rather than stopping at a plan or offering to continue. Review-only or proposal-only requests remain read-only unless the user requests edits.
- Before asking a question, consult the current conversation, relevant project files, and existing decisions. Reuse explicit choices and approvals while their scope remains unchanged; do not ask again merely because a new turn or workflow step begins.
- Resolve routine, reversible implementation choices within the authorized task and briefly state material assumptions. Ask only when missing information materially changes correctness or scope and cannot be established from available evidence. Do not invent research facts, results, credentials, or approvals to fill a gap.
- Distinguish agent verification, progress notification, optional clarification, and required user approval. Verification is performed by the agent; notification does not require a reply. A missing answer or elapsed time never counts as required approval.
- Preserve explicit approval requirements within their stated scope. Explain the exact action and applicable instruction when approval is needed. Complete authorized preparation first so the user can review a concrete result, and continue independent work while a dependent step waits.
- Use the user's current task and explicit corrections to determine scope. Apply Skills only where relevant; do not turn optional guidance into approval gates or automatically expand a local task into a full workflow. Do not edit installed Skills as part of ordinary project work.

# Completion and Verification

- Distinguish implementation or artifact completion, validation status, and user acceptance. Pending final review does not prevent preparing the complete deliverable and does not imply acceptance.
- A local blocker or a stalled polishing loop blocks only dependent work. Complete unaffected parts, report what remains and why, and never mark the overall task complete while required work remains.
- Match verification effort to the change and research risk. Prefer a minimal reproducible run, relevant numerical checks, leakage checks, and figure/data consistency checks. Do not run unrelated exhaustive tests or retrain models merely to validate documentation changes.
- Code inspection and explicitly tentative hypotheses may be used to build a reproduction. Distinguish suspected causes from verified causes and attempted fixes from verified fixes.
- After a substantive research step, update `docs/progress.md` or the corresponding versioned method/results document with relevant inputs, methods, splits, outputs, conclusions, limitations, and next steps. Keep documentation proportional to the task.

# Research Records and Current Scope

- The user reported on 2026-09-05 that the stage report had been submitted and the previous advisor requirements had expired. The former advisor-specific section has been removed; its fixed model mandates, milestone prerequisites, and case-start gates are no longer standing instructions.
- Existing methods, configurations, reports, and results describe completed or versioned work. Historical advisor notes and old next-step lists are context, not current task authorization or mandatory work. Use the current user request to determine subsequent work; retiring old requirements does not itself request new experiments or a new case.
- Preserve the traceability of existing results and retain negative findings and limitations. Do not silently rewrite frozen splits, thresholds, metrics, model records, or conclusions. Changes to research design belong in a separately documented version within the user's authorized task; do not use evaluation outcomes to conceal failures through retrospective tuning or relabeling.
- Claims of effectiveness require supporting evidence and appropriate evaluation; successful execution alone is not evidence of scientific validity. Distinguish model dependence from causality, proxy outcomes from observed event truth, and exploratory results from confirmatory findings.

# Commit Guidelines

- Do NOT add `Co-Authored-By` lines to any commit messages.
- Format: `type: description` — English, lowercase, concise.

# Git Rules

- Do NOT commit files under `docs/superpowers/` or any superpowers-generated documentation into git.
- Do NOT create test-related commits (commits with `test:` prefix). Test changes should be squashed into or amended to the feature/fix commit they relate to.
- Do NOT create pull requests. Push directly to main — this is a personal repository.
