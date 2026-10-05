// Feature: Natural voices auto-selection and test button.
(() => {
  const VOICE_CHOSEN_KEY = "classroom.voice_chosen_by_user";
  const EDGE_TIP_DISMISSED_KEY = "classroom.edge_tip_dismissed";
  let settled = false; // true once a natural voice was picked, or the learner chose one

  // A voice saved by the core's picker before this feature existed also counts
  // as the learner's choice: the core only saves it when the picker changes.
  const learnerChose = () => Classroom.load(VOICE_CHOSEN_KEY) || Classroom.load("classroom.voice", "");

  // Runs on load and on every voiceschanged: browsers can deliver the voice
  // list in stages (local voices first, online natural ones later).
  function decide() {
    if (settled) return;
    if (learnerChose()) { settled = true; return; }
    const voices = window.speechSynthesis.getVoices().filter(v => v.lang.startsWith("en"));
    if (!voices.length) return; // still loading

    const natural = voices.filter(v => /natural|neural/i.test(v.name));
    const bestList = natural.length ? natural : voices;
    const best = bestList.find(v => v.lang === "en-US") || bestList[0];
    Classroom.settings.voice = best.name;
    const sel = document.getElementById("voice");
    if (sel) sel.value = best.name;

    if (natural.length) {
      settled = true;
      document.getElementById("edge-tip")?.remove();
    } else if (!Classroom.load(EDGE_TIP_DISMISSED_KEY) && !document.getElementById("edge-tip")) {
      showEdgeTip();
    }
  }

  function showEdgeTip() {
    const tip = document.createElement("div");
    tip.id = "edge-tip";
    tip.style.cssText = "position: fixed; top: 1rem; right: 1rem; background: #fff3cd; border: 1px solid #ffc107; "
      + "border-radius: 4px; padding: 1rem; max-width: 300px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); "
      + "font-size: 0.9rem; z-index: 1000; color: #1f2328;";

    const text = document.createElement("p");
    text.style.margin = "0 0 0.5rem 0";
    text.textContent = "For more natural voices, open this page in Microsoft Edge.";

    const dismissBtn = document.createElement("button");
    dismissBtn.textContent = "Dismiss";
    dismissBtn.style.cssText = "background: none; border: none; color: #0c63e4; cursor: pointer; "
      + "font-size: 0.9rem; padding: 0; text-decoration: underline;";
    dismissBtn.onclick = () => {
      tip.remove();
      Classroom.save(EDGE_TIP_DISMISSED_KEY, "true");
    };

    tip.appendChild(text);
    tip.appendChild(dismissBtn);
    document.body.appendChild(tip);
  }

  function addTestVoiceButton() {
    const btn = document.createElement("button");
    btn.textContent = "Test voice";
    btn.title = "Hear the current voice with a sample sentence";
    btn.onclick = () => {
      // Never interrupt a lesson: the learner would lose their place.
      if (Classroom.speaking) {
        btn.textContent = "Reading… stop first";
        setTimeout(() => { btn.textContent = "Test voice"; }, 1500);
        return;
      }
      // "ui": play now, even while a new section waits for Claude's reply.
      Classroom.enqueue([{ text: "This is a sample sentence to test the voice.", source: "ui" }]);
    };
    Classroom.addSetting(btn);
  }

  Classroom.on("ready", () => {
    decide();
    window.speechSynthesis.addEventListener("voiceschanged", decide);
    addTestVoiceButton();

    const voiceSelect = document.getElementById("voice");
    if (voiceSelect) {
      voiceSelect.addEventListener("change", () => {
        Classroom.save(VOICE_CHOSEN_KEY, "true");
        settled = true;
      });
    }
  });
})();
