// Feature: two-voice dialogue lessons.
//
// A lesson paragraph that starts with **Teacher:** or **Student:** is shown as
// a chat bubble and read in two voices.
//
// Role convention (other features, e.g. offline voices, map these):
//   voice "A" = teacher, uses the main Voice setting
//   voice "B" = student, uses the Student voice picker
// Speech items for dialogue carry voice "A" or "B"; the speaker label is not
// spoken. For the browser engine the item also carries voiceName and pitch:
// with no student voice picked, the student shares the teacher's voice at a
// higher pitch.
(() => {
  const C = window.Classroom;
  const ROLES = { teacher: "A", student: "B" };
  const KEY = "classroom.studentVoice";
  const STUDENT_PITCH = 1.3;
  let studentVoice = C.load(KEY, ""); // "" = same voice as the teacher, higher pitch

  C.addStyle(`
    .dialogue-who { display: block; font-size: 12px; font-weight: 700; color: var(--muted); margin: 10px 10px 2px; max-width: 80%; }
    p.dialogue { margin: 0 0 10px; padding: 8px 14px; border-radius: 14px; border: 1px solid var(--border); max-width: 80%; width: fit-content; }
    p.dialogue.teacher { background: var(--code-bg); border-top-left-radius: 4px; }
    p.dialogue.student { background: var(--accent); color: var(--accent-text); border-color: var(--accent); border-top-right-radius: 4px; margin-left: auto; }
    .dialogue-who.student { text-align: right; margin-left: auto; }
    p.dialogue.speaking { box-shadow: 0 0 0 4px var(--speaking); }
  `);

  // ---- rendering: turn each dialogue paragraph into a bubble, move its label out ----
  // The <p> stays where it is (so a list item keeps its direct <p> and is not
  // read twice); the label becomes a sibling span, which is never read aloud.
  C.on("render", (main) => {
    for (const p of main.querySelectorAll("p")) {
      const label = p.firstElementChild;
      if (!label || label.tagName !== "STRONG" || p.firstChild !== label) continue;
      const who = label.textContent.trim().replace(/:$/, "").toLowerCase();
      if (!ROLES[who] || !/:\s*$/.test(label.textContent)) continue;
      label.remove();
      if (p.firstChild?.nodeType === Node.TEXT_NODE) p.firstChild.textContent = p.firstChild.textContent.replace(/^\s+/, "");
      const tag = document.createElement("span");
      tag.className = `dialogue-who ${who}`;
      tag.textContent = who === "teacher" ? "Teacher" : "Student";
      p.before(tag);
      p.classList.add("dialogue", who);
      p.dataset.voice = ROLES[who];
    }
  });

  // ---- speech: tag items with their role ----
  C.decorateItems((item) => {
    const role = item.el?.dataset?.voice;
    if (!role || !item.el.classList.contains("dialogue")) return;
    item.voice = role;
    if (role === "B") {
      if (studentVoice) item.voiceName = studentVoice;
      else item.pitch = STUDENT_PITCH; // same voice as the teacher, so shift the pitch
    }
  });

  // ---- Student voice picker ----
  const sel = document.createElement("select");
  sel.id = "student-voice";
  const label = document.createElement("label");
  label.append("Student voice ", sel);
  function fill() {
    const voices = speechSynthesis.getVoices().filter(v => v.lang.startsWith("en"));
    if (!voices.length) return; // not loaded yet; keep the saved choice
    sel.replaceChildren(new Option("Same as teacher, higher pitch", ""));
    for (const v of voices) sel.add(new Option(v.name.replace(/^Microsoft /, ""), v.name));
    if (voices.some(v => v.name === studentVoice)) sel.value = studentVoice;
    else studentVoice = "";
  }
  sel.onchange = () => { studentVoice = sel.value; C.save(KEY, studentVoice); };
  C.addSetting(label);
  fill();
  speechSynthesis.addEventListener("voiceschanged", fill);
})();
