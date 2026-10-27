// A question from the agent (`item/tool/requestUserInput`, DESKTOP_PARITY §4.5 item 23, DESKTOP §16 B2):
// each question's options with their descriptions, a free answer beside them when `isOther`, a hidden
// field when `isSecret`. The answer is spelled as `ling`'s own terminal interface spells it, so the
// agent reads the window's answers the same way: the chosen option's label, then any words the user
// wrote as `user_note: …`; "other" is the label `None of the above` with the user's words as the note.
// Pure: no DOM.

export interface QuestionOption {
  label: string;
  description: string;
}

export interface Question {
  id: string;
  header: string;
  question: string;
  isOther: boolean;
  isSecret: boolean;
  options: QuestionOption[] | null;
}

/** The label `ling` gives the free answer beside a question's options. */
export const OTHER_LABEL = "None of the above";

/** What the user has given for one question: an option (or `OTHER_LABEL`) and their words. */
export interface QuestionDraft {
  choice: string | null;
  text: string;
}

type Json = Record<string, unknown>;
const isObject = (value: unknown): value is Json => typeof value === "object" && value !== null && !Array.isArray(value);

/** The request's questions, read defensively (an older server may leave fields out). */
export function readQuestions(params: unknown): Question[] {
  const list = isObject(params) && Array.isArray(params.questions) ? params.questions : [];
  return list.filter(isObject).filter((question) => typeof question.id === "string").map((question) => ({
    id: String(question.id),
    header: typeof question.header === "string" ? question.header : "",
    question: typeof question.question === "string" ? question.question : "",
    isOther: question.isOther === true,
    isSecret: question.isSecret === true,
    options: Array.isArray(question.options) && question.options.length
      ? question.options.filter(isObject).map((option) => ({ label: String(option.label ?? ""), description: typeof option.description === "string" ? option.description : "" }))
      : null,
  }));
}

/** Whether a question shows a field for words: no options, or "other" chosen. */
export function wantsText(question: Question, draft: QuestionDraft | undefined): boolean {
  return !question.options || (question.isOther && draft?.choice === OTHER_LABEL);
}

/** Whether every question has an answer: an option chosen (with words for "other"), or words. */
export function complete(questions: Question[], drafts: Record<string, QuestionDraft>): boolean {
  return questions.every((question) => {
    const draft = drafts[question.id];
    if (!question.options) return !!draft?.text.trim();
    if (!draft?.choice) return false;
    return draft.choice !== OTHER_LABEL || !!draft.text.trim();
  });
}

/** The response to the request: each question's answers as `ling` spells them. */
export function answerOf(questions: Question[], drafts: Record<string, QuestionDraft>): { answers: Record<string, { answers: string[] }> } {
  return {
    answers: Object.fromEntries(questions.map((question) => {
      const draft = drafts[question.id] ?? { choice: null, text: "" };
      const answers: string[] = [];
      if (question.options && draft.choice) answers.push(draft.choice);
      const text = draft.text.trim();
      if (text && wantsText(question, draft)) answers.push(`user_note: ${text}`);
      return [question.id, { answers }];
    })),
  };
}
