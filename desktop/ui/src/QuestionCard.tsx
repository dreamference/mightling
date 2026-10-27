// The card for a question from the agent (DESKTOP §16 B2): options as a list with their descriptions,
// "None of the above" with a field for the user's own words when the question allows it, and a hidden
// field for a secret. The logic is questions.ts.
import { useState } from "react";

import { answerOf, complete, OTHER_LABEL, type QuestionDraft, readQuestions, wantsText } from "./questions";

export function QuestionCard(props: { params: unknown; onAnswer: (result: unknown) => void }) {
  const questions = readQuestions(props.params);
  const [drafts, setDrafts] = useState<Record<string, QuestionDraft>>({});
  const set = (id: string, change: Partial<QuestionDraft>) => setDrafts((all) => ({ ...all, [id]: { ...{ choice: null, text: "" }, ...all[id], ...change } }));
  return (
    <div className="approval questions">
      {questions.map((question) => {
        const draft = drafts[question.id];
        const name = `question-${question.id}`;
        const choices = question.options ? [...question.options, ...(question.isOther ? [{ label: OTHER_LABEL, description: "Answer in your own words." }] : [])] : [];
        return (
          <fieldset key={question.id} className="question">
            <legend>{question.header ? <strong>{question.header}</strong> : null} {question.question}</legend>
            {choices.map((option) => (
              <label key={option.label} className="question-option">
                <input type="radio" name={name} checked={draft?.choice === option.label} onChange={() => set(question.id, { choice: option.label })} />
                <span>
                  <span className="question-label">{option.label}</span>
                  {option.description ? <span className="question-description">{option.description}</span> : null}
                </span>
              </label>
            ))}
            {wantsText(question, draft) ? (
              <input
                aria-label={question.options ? `Your answer to ${question.header || question.question}` : question.header || question.question}
                type={question.isSecret ? "password" : "text"}
                autoComplete="off"
                spellCheck={!question.isSecret}
                value={draft?.text ?? ""}
                onChange={(event) => set(question.id, { text: event.target.value })}
              />
            ) : null}
            {question.isSecret ? <span className="question-description">Hidden as you type; it goes to the agent and stays in the thread's record.</span> : null}
          </fieldset>
        );
      })}
      <div className="buttons">
        <button className="primary" disabled={!complete(questions, drafts)} onClick={() => props.onAnswer(answerOf(questions, drafts))}>Answer</button>
        <button title="Answer nothing: the agent goes on without it" onClick={() => props.onAnswer(answerOf(questions, {}))}>Skip</button>
      </div>
    </div>
  );
}
