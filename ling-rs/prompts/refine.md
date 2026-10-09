<!-- The texts of refine mode (specs/DREAMFERENCE_MIGHTLING_REFINE.md), one piece per marker. The
launcher composes from them (src/refine.rs); dreamference/night_shift/refine_prompt.py carries the
same pieces for Night Shift and SWE-bench, and a test keeps the two equal. A piece is the text
between its marker line and the next one, without the final line break. {subject} is "task" in the
product and "issue" in SWE-bench, whose composed prompts a test pins byte for byte. A piece whose
name ends in -v2 replaces its namesake in refine-v2 (spec §10); the others serve both versions. -->
<!-- study-intro -->
This is the first of two steps. Do not fix anything yet: study the {subject} below and write a precise
description of the problem for the second step, which will make the fix.
<!-- study-sections -->
1. Intent: what the reporter is trying to achieve, from the title and their use case, not only
   from the example they give.
2. Requirements: the behaviour the fix must produce, as observable results ("for input X the
   result is Y"). Never "it no longer raises": say what it returns or prints.
3. Code paths: every place in the repository that produces the behaviour in question, found by
   following callers and references, not only the one the example reaches. Name each by file and
   function.
4. Edge cases: inputs the example does not cover that the same fix must handle (other types,
   subclasses, ancestors, empty input, a sibling function with the same flaw).
5. Must not change: behaviour that other code or the existing tests rely on.
6. Acceptance checks: commands or short scripts that will show the fix is complete, each with
   its expected output.

Keep it factual: say what you checked in the code and what you are inferring.
<!-- study-sections-v2 -->
1. Intent: what the reporter is trying to achieve, from the title and their use case, not only
   from the example they give. Say whether it changes behaviour observable today, and which
   outputs, side effects the request implies included.
2. Requirements: the behaviour the fix must produce, as observable results ("for input X the
   result is Y"). Never "it no longer raises": say what it returns or prints. Where the request
   leaves a choice open, name one option, the one this codebase's sibling code uses, with a
   one-line reason; never a list of alternatives.
3. Code paths: every place in the repository that produces the behaviour in question, found by
   following callers and references, not only the one the example reaches. Name each by file and
   function.
4. Edge cases: inputs the example does not cover that the same fix must handle (other types,
   subclasses, ancestors, empty input, a sibling function with the same flaw).
5. Must not change: behaviour that other code or the existing tests rely on, inside or outside
   the code paths the request touches, unless the request contradicts it. Then
   "Expected to change": each existing test or documented behaviour the request contradicts,
   with its new expected value.
6. Acceptance checks: commands or short scripts that will show the fix is complete, each with
   its expected output, on inputs whose result the bug changes: a check that passes before the
   fix checks nothing.

Before you finish, check each claim by reading or running the code: every file, function and
code path named, the current behaviour, any "already fixed upstream". Delete what you could not
confirm; mark an inference as one.
<!-- study-code-index -->
- Find the code with the `code_*` tools: `code_search` with the {subject}'s words, then
  `code_callers` and `code_refs` to find every other path that produces the same behaviour.
<!-- study -->
{intro}

- You may read the code, run it and run the repository's tests. Do not change any file in the
  repository: {writes}
- Nobody will answer questions: where something is unclear, make the reasonable choice and say
  which you made.
{code_index}
Write the description as your final message, in six sections:
{sections} The final message is all the second step receives, so it must stand on its own.

Task:
{task}
<!-- fix-rules -->
- A first step studied the {subject} and wrote the refined description that follows it. Use it to
  see the whole problem, but the {subject} is authoritative: where the two disagree, follow the {subject}.
- Fix the cause, not the symptom: no guard (a length check, a try/except, an early return) that
  only hides the reported failure.
- Before you stop, run every acceptance check in the refined description and confirm that each
  gives its expected result.
<!-- fix-rules-v2 -->
- A first step studied the {subject} and wrote the refined description that follows it. Use it to
  see the whole problem, but the {subject} is authoritative: where the two disagree, follow the {subject}.
- Fix the cause, not the symptom: no guard (a length check, a try/except, an early return) that
  only hides the reported failure.
- What the description lists as expected to change is meant to change: give it the new value the
  {subject} asks for, and do not keep the old one.
- Before you stop, run every acceptance check in the refined description and confirm that each
  gives its expected result.
<!-- refined-heading -->
Refined description (written by the first step; it may be incomplete or wrong):
<!-- no-refined -->
(The first step wrote no description: work from the {subject} alone.)
<!-- fix -->
{rules}

{heading}
{refined}
<!-- end -->
