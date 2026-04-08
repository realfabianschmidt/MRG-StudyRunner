# Project Rules

This document replaces the imported engineering standards from the other software project.

It keeps only the rules that are useful for this small Study Runner project. The goal is not maximum complexity. The goal is a project that stays readable for researchers, designers, and teammates with little or no coding experience.

## Why these rules exist

- The project should stay easy to read.
- People without a coding background should still understand what each file is for.
- New changes should make the project clearer, not harder to work with.
- Plain language is more important than technical jargon.

## Language rule

- Required: All active documentation must be written in English.
- Required: All code comments must be written in English.
- Required: All implementation plans, developer notes, and work instructions must be written in English.
- Required: New file names for documentation should be clear English names.
- Note: User-facing study text may use the language that fits the study, but developer-facing material stays in English.

## Key terms

- `Handler`: A small function that reacts to a click, request, or timer.
- `Service`: A file or function with one clear job, such as loading config or saving results.
- `Adapter`: A small bridge to external tools such as BrainBit or TouchDesigner.
- `API`: Fixed web addresses that let the browser pages and server talk to each other.
- `Validation`: Checking whether incoming data is complete and sensible before it is saved or used.

## 1. Keep it simple

- Required: Build direct, simple solutions.
- Required: Do not introduce a plugin architecture.
- Required: Do not add structure for a possible future use case unless there is a clear need now.
- Required: If a term is hard to understand, replace it or explain it immediately.

## 2. Use clear names

- Required: File names, headings, variables, and function names should describe their real job.
- Required: Avoid unnecessary abbreviations.
- Required: If an abbreviation must stay, explain it the first time it appears in the README or docs.
- Required: New documents and plans must use clear, readable names.
- Recommended: Prefer `results_service.py` over a vague file name like `utils.py`.

## 3. Keep responsibilities small

- Required: One file should have one main purpose.
- Required: A handler should react and then call the matching business logic.
- Required: Saving, validation, rendering, and hardware control should not be mixed inside one long function.
- Required: If a function has several steps, split them into named helper steps.

## 4. Write comments that help

- Required: Comments should explain why something is done.
- Required: Comments should not repeat what the code already says clearly.
- Required: Add short comments for non-obvious areas such as timer logic, privacy boundaries, or hardware fallback behavior.
- Required: Remove or update stale comments right away.

## 5. Keep data flow safe and clear

- Required: Anything that comes from forms, JSON files, or browser requests must be validated before it is saved.
- Required: Broken or incomplete data must not silently count as success.
- Required: Validation errors must return a clear message to the browser.
- Required: Saved results must not contain direct personal information.
- Required: Participant input must never be inserted into the browser through unsafe HTML paths such as `innerHTML`.
- Required: Card templates may use HTML string rendering only when dynamic text is escaped first.
- Required: Researcher-authored stimulus `html` and `js` trigger content is the only trusted exception and must stay clearly documented as trusted lab content.

## 6. Keep frontend and backend handlers thin

- Required: Frontend handlers stay small.
- Required: Backend handlers stay small.
- Required: Reusable logic goes into services or helper functions, not into click handlers.
- Recommended: A reader should be able to understand the rough purpose of a handler within a few seconds.

## 7. Stay extensible without plugins

- Required: New question types should always follow the same order.
- First define the default data.
- Then add the admin-side rendering.
- Then add the study-side rendering.
- Then add answer collection.
- Then update validation and documentation.
- Required: External tools such as BrainBit or TouchDesigner should live in small adapter files.
- Required: Extend the existing simple path instead of building a second system next to it.

## 8. Write documents for humans

- Required: Active project documents should use simple English.
- Required: Explain important terms the first time they appear.
- Required: The README, overview docs, and plans should be readable without a strong coding background.
- Required: Every important file and folder should be explained at least once.
- Recommended: A document should be understandable in a few minutes.

## 9. Check before calling work done

- Required: After changes, do a short check that startup, configuration, stimulus flow, question flow, and result saving still work.
- Required: If something could not be tested, say so clearly.
- Recommended: Repeated manual checks should later become small automated tests where useful.

## 10. What this project does not need

- No plugin architecture
- No microservices
- No heavy framework just to look architecturally clean
- No abstract names copied from unrelated projects
- No documents that only specialists can understand

## When a change is really done

A change is done when:

- the behavior is correct
- the names are still clear
- important terms are explained
- relevant error cases were considered
- the docs still match reality
- a short verification pass happened
