// Offline voices: Kokoro-82M running in the browser through kokoro-js.
// Off by default. When switched on it loads the library and model from a CDN
// (one-time download, then cached by the browser) and replaces the speech
// engine. Any failure falls back to the browser engine with a visible notice.
(() => {
  const C = window.Classroom;
  const LIB_URL = "https://cdn.jsdelivr.net/npm/kokoro-js@1.2.1/+esm";
  const MODEL_ID = "onnx-community/Kokoro-82M-v1.0-ONNX";
  const VOICES = ["af_heart", "af_bella", "af_nicole", "af_sarah", "af_kore",
    "am_michael", "am_fenrir", "am_puck", "bf_emma", "bm_george", "bm_fable"];
  const chosen = { A: C.load("classroom.kokoro.a", "af_heart"), B: C.load("classroom.kokoro.b", "am_michael") };

  // ---------- header controls ----------
  C.addStyle(`
    .kk-wrap { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
    .kk-wrap > label:first-child { flex: 1; }
    .kk-gear { padding: 4px 12px; min-height: 36px; }
    .kk-status { flex-basis: 100%; font-size: 13px; color: var(--muted); }
    .kk-status:empty { display: none; }
    .kk-status.warn { color: var(--danger); }
    .kk-pop { flex-basis: 100%; background: var(--code-bg); border-radius: 12px; padding: 10px 12px;
      display: none; flex-direction: column; gap: 8px; }
    .kk-pop.open { display: flex; }
    .kk-pop label { color: var(--text); justify-content: space-between; }
  `);
  const wrap = document.createElement("span");
  wrap.className = "kk-wrap";
  wrap.innerHTML = `<label><input type="checkbox" id="kk-toggle"> Offline voice (Kokoro)</label>
    <button id="kk-gear" class="kk-gear" title="Offline voice settings" aria-label="Offline voice settings">⚙</button>
    <span class="kk-status" id="kk-status" role="status"></span>
    <div class="kk-pop" id="kk-pop">
      <label>Teacher voice (A) <select id="kk-a"></select></label>
      <label>Student voice (B) <select id="kk-b"></select></label>
    </div>`;
  C.addSetting(wrap);
  const $ = (id) => wrap.querySelector("#" + id);
  const toggle = $("kk-toggle"), status = $("kk-status");
  for (const role of ["a", "b"]) {
    const sel = $("kk-" + role);
    sel.innerHTML = VOICES.map(v => `<option>${v}</option>`).join("");
    sel.value = chosen[role.toUpperCase()];
    sel.onchange = () => { chosen[role.toUpperCase()] = sel.value; C.save("classroom.kokoro." + role, sel.value); };
  }
  $("kk-gear").onclick = () => $("kk-pop").classList.toggle("open");
  const say = (msg, warn) => { status.textContent = msg; status.classList.toggle("warn", !!warn); };

  // ---------- engine ----------
  let tts = null;            // loaded model
  let loading = null;        // load promise
  let ctx = null;            // AudioContext
  let source = null;         // currently playing source
  let useFallback = false;   // load or synthesis failed: delegate to the browser engine
  let paused = false;
  let busy = false;          // an item is being generated or played
  let held = null;           // generated audio waiting for resume
  let gen = 0;               // invalidates callbacks from cancelled items
  let chain = Promise.resolve(); // the model runs one synthesis at a time
  const cache = new Map();   // key -> promise of audio

  const speedFor = (item) => Math.min(2, Math.max(0.5, C.settings.rate * (item.rate || 1)));
  const voiceFor = (item) => item.voice === "B" ? chosen.B : chosen.A;
  const keyOf = (item) => `${voiceFor(item)}|${speedFor(item)}|${item.text}`;

  function synth(item) {
    const key = keyOf(item);
    if (!cache.has(key)) {
      const p = chain.then(() => tts.generate(item.text, { voice: voiceFor(item), speed: speedFor(item) }));
      chain = p.catch(() => {});
      cache.set(key, p);
    }
    return cache.get(key);
  }

  function fail(why) {
    useFallback = true;
    toggle.checked = false;
    C.save("classroom.kokoro", "false");
    say(`Offline voice failed (${why}). Using the browser voice.`, true);
  }

  function play(audio, done) {
    ctx ||= new AudioContext();
    const buf = ctx.createBuffer(1, audio.audio.length, audio.sampling_rate);
    buf.copyToChannel(audio.audio, 0);
    const src = ctx.createBufferSource();
    src.buffer = buf;
    src.connect(ctx.destination);
    src.onended = () => { if (source === src) source = null; done(); };
    source = src;
    src.start();
  }

  const engine = {
    name: "kokoro",
    speak(item, done) {
      if (useFallback) return C.defaultEngine.speak(item, done);
      const mine = ++gen;
      busy = true;
      const finish = () => { if (mine === gen) { busy = false; done(); } };
      synth(item).then((audio) => {
        if (mine !== gen) return;
        cache.delete(keyOf(item));
        if (paused) held = () => play(audio, finish);   // pressed pause while generating
        else play(audio, finish);
        const upcoming = C.peek?.();   // generate the next item while this one plays
        if (upcoming) synth(upcoming).catch(() => {});
      }).catch((e) => {
        if (mine !== gen) return;
        busy = false;
        fail(e?.message || "synthesis error");
        C.defaultEngine.speak(item, done);
      });
    },
    cancel() {
      gen++; paused = false; busy = false; held = null; cache.clear();
      if (source) { const s = source; source = null; s.onended = null; try { s.stop(); } catch {} }
      if (ctx && ctx.state === "suspended") ctx.resume();
      C.defaultEngine.cancel();
    },
    pause() {
      if (useFallback) return C.defaultEngine.pause();
      if (!busy) return;
      paused = true;
      if (source) ctx.suspend();
    },
    resume() {
      if (useFallback) return C.defaultEngine.resume();
      paused = false;
      if (held) { const h = held; held = null; h(); }
      else ctx?.resume();
    },
    paused() { return useFallback ? C.defaultEngine.paused() : paused; },
  };

  // Chat can jump ahead of the item prefetched above. chatEntry fires just before
  // core queues the entry, so look again once it has: the chat item then starts
  // generating while this one plays (behind the earlier prefetch on the chain).
  C.on("chatEntry", () => setTimeout(() => {
    if (!busy || useFallback || !tts) return;
    const upcoming = C.peek?.();
    if (upcoming) synth(upcoming).catch(() => {});
  }, 0));

  // ---------- loading ----------
  async function load() {
    say("Loading offline voice…");
    const { KokoroTTS } = await import(LIB_URL);
    const progress_callback = (p) => {
      if (p?.status === "progress" && typeof p.progress === "number")
        say(`Downloading voice model… ${Math.round(p.progress)}%`);
    };
    const gpu = !!navigator.gpu && !!(await navigator.gpu.requestAdapter().catch(() => null));
    if (gpu) {
      try { return await KokoroTTS.from_pretrained(MODEL_ID, { dtype: "fp32", device: "webgpu", progress_callback }); }
      catch (e) { console.warn("[kokoro] WebGPU failed, trying WASM", e); }
    }
    return KokoroTTS.from_pretrained(MODEL_ID, { dtype: "q8", device: "wasm", progress_callback });
  }

  async function enable() {
    useFallback = false;
    C.save("classroom.kokoro", "true");
    try {
      if (!tts) tts = await (loading ||= load());
    } catch (e) {
      loading = null;
      console.error("[kokoro]", e);
      if (toggle.checked) fail(e?.message || "could not load");
      return;
    }
    if (!toggle.checked) return;   // switched off while loading
    say("Offline voice ready.");
    C.setEngine(engine, { keep: true });
  }

  function disable() {
    C.save("classroom.kokoro", "false");
    say("");
    C.setEngine(null, { keep: true });
  }

  toggle.onchange = () => (toggle.checked ? enable() : disable());
  if (C.load("classroom.kokoro", "false") === "true") { toggle.checked = true; enable(); }
})();
