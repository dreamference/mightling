// A question from the agent answered in full (DESKTOP §16 B2).
import { describe, expect, it } from "vitest";

import { answerOf, complete, OTHER_LABEL, readQuestions, wantsText } from "./questions";

const params = {
  threadId: "t",
  questions: [
    { id: "scope", header: "Scope", question: "Which part first?", isOther: true, isSecret: false, options: [{ label: "The client", description: "the retry loop" }, { label: "The tests", description: "" }] },
    { id: "token", header: "Token", question: "The token?", isOther: false, isSecret: true, options: null },
    { id: "name", header: "", question: "A name?", isOther: false, isSecret: false, options: [] },
  ],
};

describe("questions", () => {
  it("are read with their options' descriptions; an empty option list is a free answer", () => {
    const questions = readQuestions(params);
    expect(questions[0].options?.[0]).toEqual({ label: "The client", description: "the retry loop" });
    expect(questions[1]).toMatchObject({ isSecret: true, options: null });
    expect(questions[2].options).toBeNull();
    expect(readQuestions({})).toEqual([]);
    expect(readQuestions({ questions: [{ header: "no id" }] })).toEqual([]);
  });

  it("show a field for words with no options, or with None of the above chosen", () => {
    const [scope, token] = readQuestions(params);
    expect(wantsText(token, undefined)).toBe(true);
    expect(wantsText(scope, { choice: "The client", text: "" })).toBe(false);
    expect(wantsText(scope, { choice: OTHER_LABEL, text: "" })).toBe(true);
  });

  it("are complete when each has an option, or words, and None of the above has words", () => {
    const questions = readQuestions(params);
    expect(complete(questions, {})).toBe(false);
    const drafts = { scope: { choice: OTHER_LABEL, text: " " }, token: { choice: null, text: "x" }, name: { choice: null, text: "Ada" } };
    expect(complete(questions, drafts)).toBe(false);
    expect(complete(questions, { ...drafts, scope: { choice: OTHER_LABEL, text: "logs" } })).toBe(true);
    expect(complete(questions, { ...drafts, scope: { choice: "The tests", text: "" } })).toBe(true);
  });

  it("are answered as ling's terminal interface answers them", () => {
    const questions = readQuestions(params);
    expect(answerOf(questions, { scope: { choice: "The client", text: "stale words" }, token: { choice: null, text: " abc " }, name: { choice: null, text: "" } })).toEqual({
      answers: { scope: { answers: ["The client"] }, token: { answers: ["user_note: abc"] }, name: { answers: [] } },
    });
    expect(answerOf(questions, { scope: { choice: OTHER_LABEL, text: "the logs" } }).answers.scope).toEqual({ answers: [OTHER_LABEL, "user_note: the logs"] });
    // Skip: nothing for any question.
    expect(answerOf(questions, {})).toEqual({ answers: { scope: { answers: [] }, token: { answers: [] }, name: { answers: [] } } });
  });
});
