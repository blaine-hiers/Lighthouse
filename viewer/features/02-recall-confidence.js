// Recall confidence: a quiz question headed "Confidence" renders as a compact
// row of three pills and is spoken as one short question, not as numbered options.
(() => {
  Classroom.addStyle(`
    .quiz-q.confidence ol { list-style: none; display: flex; gap: 8px; flex-wrap: wrap; padding-left: 0; }
    .quiz-q.confidence li { margin: 0; padding: 2px 12px; border: 1px solid var(--accent); border-radius: 999px; font-size: 14px; }
    .quiz-q.confidence li.chosen { background: var(--accent); color: var(--accent-text); }
  `);

  Classroom.on("quizQuestion", (q, qd, ctx) => {
    if ((q.header || "").trim().toLowerCase() !== "confidence") return;
    qd.classList.add("confidence");
    ctx.speak = "How sure are you? One to three.";
  });
})();
