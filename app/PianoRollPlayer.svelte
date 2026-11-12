<script>
  // npm i tone @tonejs/midi smplr soundfont2 @xromdev/pianoroll
  import { onMount, onDestroy, untrack, tick } from 'svelte';

  /* 
    ========================================================================
    HOW TO USE PianoRollPlayer.svelte:
    ========================================================================
    1. Import the component into your Svelte file:
       import PianoRollPlayer from './PianoRollPlayer.svelte';

    2. Pass props to configure it:
       - midi: URL string to a .mid/.midi file, or an ArrayBuffer.
       - soundfont: URL string to an .sf2/.sf3 file (or false to disable).
       - height: Height of the piano roll canvas in pixels (default: 320).
       - midiPicker: Boolean to show/hide the built-in MIDI file picker (default: true).
       - sfPicker: Boolean to show/hide the built-in SoundFont file picker (default: true).
       - onready: Callback function triggered when player is fully initialized.
       - onstatus: Callback for status message updates.
       - onerror: Callback for error handling.

    3. Exposed Public Methods (via bind:this):
       - player.loadMidiFile(src) / player.changeMidi(src)
       - player.loadSoundfontFile(src) / player.changeSoundfont(src)
       - player.play()
       - player.pause()
       - player.goto(seconds)
       - player.refresh()
    ========================================================================
  */

  let {
    midi = null,
    soundfont = null,
    height = 320,
    midiPicker = true,
    sfPicker = true,
    onready = () => {},
    onstatus = () => {},
    onerror = () => {},
  } = $props();

  const HUES = [210, 160, 280, 30, 340, 80];
  const SYNTH_LABEL = 'Basic synth';
  const fmt = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;

  // ---- lazy libs (browser only, SSR safe) ----
  let libsPromise;
  function getLibs() {
    return (libsPromise ??= (async () => {
      const [Tone, midiMod, smplr, sf2, pr] = await Promise.all([
        import('tone'),
        import('@tonejs/midi'),
        import('smplr'),
        import('soundfont2'),
        import('@xromdev/pianoroll'),
      ]);
      if (!document.getElementById('pianoroll-lib-css')) {
        for (const u of [
          'https://cdn.jsdelivr.net/npm/@xromdev/pianoroll/dist/pianoroll.css',
          'https://unpkg.com/@xromdev/pianoroll/dist/pianoroll.css',
        ]) {
          try {
            const r = await fetch(u);
            if (r.ok) {
              const el = document.createElement('style');
              el.id = 'pianoroll-lib-css';
              el.textContent = await r.text();
              document.head.appendChild(el);
              break;
            }
          } catch {}
        }
      }
      return {
        Tone,
        Midi: midiMod.Midi,
        Sampler: smplr.Soundfont2Sampler,
        SF2: sf2.SoundFont2 ?? sf2.default?.SoundFont2 ?? sf2.default,
        View: pr.PianoRollView,
      };
    })());
  }

  // ---- reactive UI state ----
  let status = $state('');
  let isLoading = $state(false);
  let loadingMessage = $state('Loading...');
  let playing = $state(false);
  let seekValue = $state(0);
  let timeText = $state('0:00 / 0:00');
  let midiLabel = $state('Choose file…');
  let sfLabel = $state('Choose file…');
  let drag = $state(false);
  let tracks = $state({});
  let vis = $state({});
  let instrumentNames = $state([]);
  let pop = $state(null);
  let popQuery = $state('');
  let popIdx = $state(0);
  let mounted = $state(false);

  // ---- non-reactive internals ----
  let rollEl, popSearchEl, popListEl;
  let notes = [];
  let duration = 0;
  let roll = null;
  let raf = null;
  let synth = null;
  let samplers = {};
  let samplerPresets = {};
  let touched = new Set();   // track keys whose instrument the user explicitly chose
  let sfUrl = null;
  let midiBuf = null;
  let lastMidiProp, lastSfProp;

  const soundfontEnabled = $derived(soundfont !== false && soundfont !== 'false');
  const hasFont = $derived(soundfontEnabled && instrumentNames.length > 0);

  // Determines whether either picker is enabled to display the top container without margin/borders if empty
  const showTopBar = $derived(midiPicker || (sfPicker && soundfontEnabled));

  const filtered = $derived(
    pop
      ? pop.items
          .map((it, i) => ({ ...it, n: i }))
          .filter((it) => !popQuery.trim() || it.label.toLowerCase().includes(popQuery.trim().toLowerCase()))
      : []
  );

  function say(msg) {
    status = msg || '';
    if (!msg) return;
    const detail = { message: msg };
    if (/error|failed|no notes|load a midi/i.test(msg)) onerror(detail);
    else onstatus(detail);
  }

  const nameOf = (u) => {
    try { return decodeURIComponent(new URL(u, location.href).pathname.split('/').pop()); }
    catch { return ''; }
  };

  // ---------- shared synth ----------
  async function ensureSynth() {
    if (synth) return synth;
    const { Tone } = await getLibs();
    synth = new Tone.PolySynth(Tone.Synth, { maxPolyphony: 64 }).toDestination();
    return synth;
  }

  // ---------- lifecycle ----------
  onMount(async () => {
    try {
      isLoading = true;
      loadingMessage = 'Initializing player...';
      await getLibs();
      await ensureSynth();
      if (typeof soundfont === 'string' && soundfont && soundfontEnabled) {
        const n = nameOf(soundfont); if (n) sfLabel = n;
        lastSfProp = soundfont;
        await loadSoundfont(soundfont);
      }
      if (midi) {
        const n = nameOf(midi); if (n) midiLabel = n;
        lastMidiProp = midi;
        await loadMidi(midi);
      }
      mounted = true;
      onready({ id: null });
    } catch (e) {
      say('Player error: ' + e.message);
    } finally {
      isLoading = false;
    }
  });

  onDestroy(async () => {
    closePicker();
    stopLoop();
    disposeSamplers();
    synth?.dispose?.();
    synth = null;
    if (libsPromise) {
      const { Tone } = await libsPromise;
      Tone.getTransport().stop();
      Tone.getTransport().cancel();
    }
  });

  $effect(() => {
    const m = midi;
    if (!mounted || !m || m === lastMidiProp) return;
    lastMidiProp = m;
    untrack(() => { midiBuf = null; midiLabel = nameOf(m) || midiLabel; loadMidi(m); });
  });
  $effect(() => {
    const s = soundfont;
    if (!mounted || s === lastSfProp) return;
    lastSfProp = s;
    untrack(() => {
      if (soundfontEnabled && s) { sfLabel = nameOf(s) || sfLabel; loadSoundfont(s); }
      else { sfUrl = null; instrumentNames = []; disposeSamplers(); }
    });
  });
  $effect(() => {
    popIdx; filtered;
    popListEl?.children[popIdx]?.scrollIntoView({ block: 'nearest' });
  });
  $effect(() => {
    if (!pop) return;
    const anchor = pop.anchor;
    const onDown = (e) => {
      const p = e.composedPath();
      if (p.includes(popSearchEl?.parentElement) || p.includes(anchor)) return;
      closePicker();
    };
    const onScroll = (e) => { if (!e.composedPath().includes(popSearchEl?.parentElement)) closePicker(); };
    const onResize = () => closePicker();
    document.addEventListener('pointerdown', onDown, true);
    window.addEventListener('scroll', onScroll, true);
    window.addEventListener('resize', onResize);
    return () => {
      document.removeEventListener('pointerdown', onDown, true);
      window.removeEventListener('scroll', onScroll, true);
      window.removeEventListener('resize', onResize);
    };
  });

  // ---------- public API ----------
  export const loadMidiFile = (src) => loadMidi(src);
  export const changeMidi = (src) => loadMidi(src);
  export const loadSoundfontFile = (src) => (soundfontEnabled ? loadSoundfont(src) : false);
  export const changeSoundfont = loadSoundfontFile;
  export const play = () => toggle(true);
  export const pause = () => toggle(false);
  export async function goto(seconds) {
    return seekTo(Number(seconds) || 0);
  }

  // ---------- file picking ----------
  async function pickMidiFile(f) {
    if (!midiPicker) return;
    midiBuf = await f.arrayBuffer();
    midiLabel = f.name;
    await loadMidi(midiBuf);
  }
  async function pickSfFile(f) {
    if (!soundfontEnabled || !sfPicker) return;
    sfUrl = URL.createObjectURL(f);
    sfLabel = f.name;
    await loadSoundfont(sfUrl);
  }
  async function onMidiChange(e) {
    const f = e.target.files[0]; if (!f) return;
    e.target.value = '';
    await pickMidiFile(f);
  }
  async function onSfChange(e) {
    const f = e.target.files[0]; if (!f) return;
    e.target.value = '';
    await pickSfFile(f);
  }
  function onDrag(e) {
    if (!e.dataTransfer?.types?.includes('Files')) return;
    e.preventDefault();
    drag = true;
  }
  function onDragLeave(e) {
    if (!e.currentTarget.contains(e.relatedTarget)) drag = false;
  }
  async function onDrop(e) {
    if (!e.dataTransfer?.files?.length) return;
    e.preventDefault();
    drag = false;
    for (const f of e.dataTransfer.files) {
      const n = f.name.toLowerCase();
      if (/\.(sf2|sf3)$/.test(n) && sfPicker) await pickSfFile(f);
      else if (/\.(mid|midi)$/.test(n) && midiPicker) await pickMidiFile(f);
    }
  }

  // ---------- picker popover ----------
  function openPicker(anchor, items, current, onPick) {
    closePicker();
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth, vh = window.innerHeight;
    const w = Math.min(Math.max(r.width, 260), vw - 16);
    const left = Math.max(8, Math.min(r.left, vw - w - 8));
    const below = vh - r.bottom - 12, above = r.top - 12;
    let top, mh;
    if (below >= 180 || below >= above) { mh = Math.min(300, below); top = r.bottom + 4; }
    else { mh = Math.min(300, above); top = r.top - 4 - mh; }
    popQuery = '';
    popIdx = Math.max(0, items.findIndex((i) => i.value === current));
    pop = {
      anchor, items, current, onPick,
      style: `left:${left}px;top:${top}px;width:${w}px;max-height:${mh}px`,
      listMax: Math.max(80, mh - 38),
    };
    tick().then(() => popSearchEl?.focus({ preventScroll: true }));
  }
  function closePicker() { pop = null; }
  function commitPicker(i) {
    const it = filtered[i];
    if (!pop || !it) return;
    const { onPick, anchor } = pop;
    closePicker();
    onPick(it.value);
    anchor?.focus();
  }
  function movePicker(d) {
    if (!filtered.length) return;
    popIdx = (popIdx + d + filtered.length) % filtered.length;
  }
  function onPopKey(e) {
    if (!pop) return;
    if (e.key === 'ArrowDown') { e.preventDefault(); movePicker(1); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); movePicker(-1); }
    else if (e.key === 'Enter') { e.preventDefault(); commitPicker(popIdx); }
    else if (e.key === 'Escape') { e.preventDefault(); const a = pop.anchor; closePicker(); a?.focus(); }
    else if (e.key === 'Tab') closePicker();
  }

  function onPickClick(e, key) {
    const btn = e.currentTarget;
    if (pop && pop.anchor === btn) return closePicker();
    const tr = tracks[key];
    const items = [{ label: SYNTH_LABEL, value: null }, ...instrumentNames.map((n) => ({ label: n, value: n }))];
    openPicker(btn, items, tr.instrument, async (v) => {
      touched.add(key);              // user made an explicit choice
      tr.instrument = v || null;
      if (tr.instrument) {
        say(`Track "${tr.label}": ${tr.instrument}`);
        await setTrackInstrument(key, tr.instrument);
      } else {
        disposeSampler(key);
        say(`Track "${tr.label}": basic synth`);
      }
    });
  }
  const instLabel = (tr) =>
    !hasFont ? '(no soundfont)' : tr.instrument && instrumentNames.includes(tr.instrument) ? tr.instrument : SYNTH_LABEL;

  function toggleVis(key) {
    vis[key] = !vis[key];
    applyFilter();
  }

  // ---------- refresh ----------
  export async function refresh() {
    if (!notes.length) return;
    isLoading = true;
    loadingMessage = 'Reloading…';
    stopLoop();
    try {
      const { Tone, View } = await getLibs();
      const tp = Tone.getTransport();
      const wasPlaying = tp.state === 'started';
      const seconds = tp.seconds;
      tp.pause();
      tp.cancel();

      rollEl.innerHTML = '';
      roll = new View(rollEl, { lookAhead: 2.0, tracks: $state.snapshot(tracks) });
      applyFilter();
      await schedule();
      paint();

      tp.seconds = Math.min(seconds, duration);
      if (wasPlaying) {
        await Tone.start();
        tp.start();
        startLoop();
        playing = true;
      } else playing = false;
      say('');
    } finally {
      isLoading = false;
    }
  }

  // ---------- MIDI ----------
  async function loadMidi(src) {
    if (!src) return;
    const prevVis = { ...vis };
    const prevInst = Object.fromEntries(Object.entries(tracks).map(([k, v]) => [k, v.instrument]));

    isLoading = true;
    loadingMessage = 'Loading MIDI...';
    say('Loading MIDI...');
    try {
      const { Tone, Midi, View } = await getLibs();
      await ensureSynth();
      const buf = typeof src === 'string' ? await (await fetch(src)).arrayBuffer() : src;
      const m = new Midi(buf);
      Tone.getTransport().stop();
      Tone.getTransport().cancel();
      stopLoop();
      playing = false;
      disposeSamplers();

      notes = [];
      const newTracks = {};
      const newVis = {};
      let i = 0;
      m.tracks.forEach((tr, idx) => {
        if (tr.instrument.percussion || !tr.notes.length) return;
        const key = `t${idx}`;
        newTracks[key] = {
          color: `hsl(${HUES[i++ % HUES.length]}, 70%, 55%)`,
          label: tr.name?.trim() || `Track ${idx + 1}`,
          instrument: prevInst[key] || null,
        };
        newVis[key] = key in prevVis ? prevVis[key] : true;
        for (const n of tr.notes) {
          notes.push({ midi: n.midi, start: n.time, duration: Math.max(n.duration, 0.05), velocity: n.velocity, track: key });
        }
      });
      tracks = newTracks;
      vis = newVis;

      notes.sort((a, b) => a.start - b.start);
      duration = notes.reduce((mx, n) => Math.max(mx, n.start + n.duration), 0);
      if (!notes.length) { say('No notes found.'); return; }

      await tick();
      rollEl.innerHTML = '';
      roll = new View(rollEl, { lookAhead: 2.0, tracks: $state.snapshot(tracks) });
      applyFilter();
      await schedule();
      paint();

      if (sfUrl) await ensureTrackSamplers();
      say(sfUrl ? '' : 'No soundfont, basic synth');
    } catch (e) {
      console.error(e);
      say('MIDI error: ' + e.message);
    } finally {
      isLoading = false;
    }
  }

  function applyFilter() {
    if (!roll) return;
    roll.setNotes(notes.filter((n) => vis[n.track]));
    paint();
  }

  // ---------- SoundFont / per-track samplers ----------
  async function makeSampler(url) {
    const { Tone, Sampler, SF2 } = await getLibs();
    const s = new Sampler(Tone.getContext().rawContext, { url, createSoundfont: (d) => new SF2(d) });
    await s.load;
    return s;
  }

  async function loadSoundfont(url) {
    if (!url || !soundfontEnabled) return;
    const prevInst = Object.fromEntries(Object.entries(tracks).map(([k, v]) => [k, v.instrument]));
    isLoading = true;
    loadingMessage = 'Loading soundfont...';
    say('Loading soundfont...');
    try {
      disposeSamplers();
      const probe = await makeSampler(url);
      sfUrl = url;
      instrumentNames = probe.instrumentNames || [];
      probe.dispose?.();

      if (notes.length) {
        for (const [key, tr] of Object.entries(tracks)) {
          if (touched.has(key)) {
            // user explicitly chose — keep it
            if (tr.instrument && instrumentNames.includes(tr.instrument)) {
              await setTrackInstrument(key, tr.instrument);
            } else {
              tr.instrument = null;
              disposeSampler(key);
            }
          } else if (prevInst[key] && instrumentNames.includes(prevInst[key])) {
            tr.instrument = prevInst[key];
            await setTrackInstrument(key, tr.instrument);
          } else {
            tr.instrument = instrumentNames[0] || null;
            if (tr.instrument) await setTrackInstrument(key, tr.instrument);
          }
        }
      }
      say(`Soundfont loaded (${instrumentNames.length} presets)`);
    } catch (e) {
      console.error(e);
      sfUrl = null;
      instrumentNames = [];
      disposeSamplers();
    // SoundFont is optional; silently keep using the built-in synth fallback.
    say('');
    } finally {
      isLoading = false;
    }
  }

  async function ensureTrackSamplers() {
    for (const [key, tr] of Object.entries(tracks)) {
      if (tr.instrument) await setTrackInstrument(key, tr.instrument);
    }
  }

  async function setTrackInstrument(key, preset) {
    if (!sfUrl || !preset) return;
    const { Tone, Sampler, SF2 } = await getLibs();
    if (samplers[key] && samplerPresets[key] === preset) return;
    if (samplers[key]) disposeSampler(key);
    try {
      const s = new Sampler(Tone.getContext().rawContext, { url: sfUrl, createSoundfont: (d) => new SF2(d) });
      await s.load;
      await s.loadInstrument(preset);
      samplers[key] = s;
      samplerPresets[key] = preset;
      say(`${tracks[key].label}: ${preset}`);
    } catch (e) {
      console.error(e);
      samplers[key] = null;
      samplerPresets[key] = null;
      say('Instrument error: ' + e.message);
    }
  }

  function disposeSampler(key) {
    try { samplers[key]?.dispose?.(); } catch {}
    delete samplers[key];
    delete samplerPresets[key];
  }
  function disposeSamplers() {
    for (const k of Object.keys(samplers)) disposeSampler(k);
  }

  // ---------- transport ----------
  async function schedule() {
    const { Tone } = await getLibs();
    await ensureSynth();
    const tp = Tone.getTransport();
    tp.cancel();
    for (const n of notes) tp.schedule((time) => trigger(Tone, n, time), n.start);
    tp.schedule(() => finish(), duration + 0.2);
  }

  function trigger(Tone, n, time) {
    if (!vis[n.track]) return;
    const s = samplers[n.track];
    if (s) {
      try {
        s.start({ note: n.midi, velocity: Math.round((n.velocity || 0.8) * 127), time, duration: n.duration });
        return;
      } catch {}
    }
    if (synth) {
      synth.triggerAttackRelease(Tone.Frequency(n.midi, 'midi'), n.duration, time, n.velocity || 0.8);
    }
  }

  async function toggle(force) {
    const { Tone } = await getLibs();
    const tp = Tone.getTransport();
    const shouldPlay = force ?? tp.state !== 'started';
    if (!shouldPlay) {
      tp.pause();
      stopLoop();
      playing = false;
      releaseHeld();
    } else {
      await Tone.start();
      tp.start();
      startLoop();
      playing = true;
    }
  }

  function releaseHeld() {
    try { synth?.releaseAll?.(); } catch {}
    for (const s of Object.values(samplers)) {
      try { s?.stop?.(); } catch {}
    }
  }

  async function seekTo(v) {
    const { Tone } = await getLibs();
    const tp = Tone.getTransport();
    v = Math.max(0, Math.min(v, duration));

    releaseHeld();

    const wasPlaying = tp.state === 'started';
    if (wasPlaying) tp.stop();
    tp.seconds = v;
    if (wasPlaying) {
      await Tone.start();
      tp.start();
      startLoop();
      playing = true;
    }
    paint();
    return v;
  }

  async function finish() {
    const { Tone } = await getLibs();
    Tone.getTransport().stop();
    stopLoop();
    playing = false;
    releaseHeld();
    paint();
  }

  async function onSeek(e) {
    await seekTo((e.currentTarget.value / 1000) * duration);
  }

  // ---------- animation ----------
  function startLoop() {
    const tick = () => { paint(); raf = requestAnimationFrame(tick); };
    raf = requestAnimationFrame(tick);
  }
  function stopLoop() {
    if (raf) cancelAnimationFrame(raf);
    raf = null;
  }
  async function paint() {
    const { Tone } = await getLibs();
    const t = Math.min(Tone.getTransport().seconds, duration);
    roll?.render(t);
    if (duration) seekValue = Math.round((t / duration) * 1000);
    timeText = `${fmt(t)} / ${fmt(duration)}`;
  }
</script>

<div class="prp">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="panel"
    class:drag
    ondragenter={onDrag}
    ondragover={onDrag}
    ondragleave={onDragLeave}
    ondrop={onDrop}
  >
    <!-- Top Bar: Conditionally rendered only if midiPicker or sfPicker is enabled -->
    {#if showTopBar}
      <div class="topbar">
        {#if midiPicker}
          <label class="filebtn" title="Choose MIDI file (or drop it here)">
            <span class="filebtn-tag">MIDI</span>
            <span class="filebtn-name" title={midiLabel}>{midiLabel}</span>
            <input class="hidden" type="file" accept=".mid,.midi" onchange={onMidiChange} />
          </label>
        {/if}

        {#if sfPicker && soundfontEnabled}
          <label class="filebtn" title="Choose SoundFont file (or drop it here)">
            <span class="filebtn-tag">SF2</span>
            <span class="filebtn-name" title={sfLabel}>{sfLabel}</span>
            <input class="hidden" type="file" accept=".sf2,.sf3" onchange={onSfChange} />
          </label>
        {/if}
      </div>
    {/if}

    <div class="tracks">
      {#each Object.entries(tracks) as [key, tr] (key)}
        <div class="track-row">
          <button
            type="button"
            class="track-btn"
            class:off={!vis[key]}
            aria-pressed={vis[key]}
            onclick={() => toggleVis(key)}
          >
            <span
              class="dot"
              class:off={!vis[key]}
              style={vis[key] ? `background-color:${tr.color}` : ''}
            ></span>
            {tr.label}
          </button>

          <span class="inst-wrap">
            <span>Instrument</span>
            <button
              type="button"
              class="inst-btn"
              style={`border-left-color:${tr.color}`}
              aria-haspopup="listbox"
              disabled={!midiPicker && !sfPicker}
              title={instLabel(tr)}
              onclick={(e) => onPickClick(e, key)}
            >
              <span class="inst-label">{instLabel(tr)}</span>
              <svg class="chevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
            </button>
          </span>
        </div>
      {/each}
    </div>

    <!-- Piano roll container with loading overlay and fallback text -->
    <div class="roll" bind:this={rollEl} style="height:{height}px">
      {#if notes.length === 0 && !isLoading}
        <div class="roll-empty">Midi not loaded yet</div>
      {/if}

      {#if isLoading}
        <div class="overlay">
          <svg class="spinner" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="spinner-track" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="spinner-head" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span class="overlay-text">{loadingMessage}</span>
        </div>
      {/if}
    </div>

    {#if status}
      <div class="status" role="status" aria-live="polite">{status}</div>
    {/if}

    <div class="transport">
      <button class="play-btn" aria-label={playing ? 'Pause' : 'Play'} onclick={() => toggle()}>
        {#if playing}
          <svg viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z" /></svg>
        {:else}
          <svg viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
        {/if}
      </button>
      <input
        class="seek"
        type="range"
        min="0"
        max="1000"
        value={seekValue}
        oninput={(e) => { seekValue = +e.currentTarget.value; }}
        onchange={onSeek}
      />
      <span class="time">{timeText}</span>
    </div>
  </div>

  {#if pop}
    <div class="pop" style={pop.style}>
      <input
        class="pop-search"
        type="text"
        placeholder="Search instruments…"
        spellcheck="false"
        autocomplete="off"
        bind:this={popSearchEl}
        bind:value={popQuery}
        oninput={() => (popIdx = 0)}
        onkeydown={onPopKey}
      />
      <ul class="pop-list" role="listbox" bind:this={popListEl} style="max-height:{pop.listMax}px">
        {#each filtered as it, i (it.n)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <li
            class="pop-item"
            class:active={i === popIdx}
            class:current={it.value === pop.current}
            role="option"
            aria-selected={it.value === pop.current}
            title={it.label}
            onpointermove={() => { if (popIdx !== i) popIdx = i; }}
            onclick={() => commitPicker(i)}
          >
            <span class="pop-idx">{it.value === null ? '' : it.n}</span>
            <span class="pop-label">{it.label}</span>
          </li>
        {:else}
          <li class="pop-empty">No matches</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .prp {
    display: block;
    overflow: hidden;
    background: #171717;
    color: #f5f5f5;
  }
  .prp *,
  .prp *::before,
  .prp *::after {
    box-sizing: border-box;
  }
  .hidden {
    display: none;
  }

  /* ---- panel ---- */
  .panel {
    position: relative;
    overflow: hidden;
    padding: 12px;
    border: 1px solid #404040;
    border-radius: 8px;
    background: #262626;
    font-family: ui-sans-serif, system-ui, sans-serif;
  }
  .panel.drag {
    border-color: #d4d4d4;
  }

  /* ---- top bar ---- */
  .topbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 8px;
    margin-bottom: 10px;
    padding-bottom: 4px;
  }
  .filebtn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    max-width: 200px;
    overflow: hidden;
    padding: 4px 10px;
    border: 1px solid #525252;
    border-radius: 4px;
    background: #404040;
    color: #f5f5f5;
    font-size: 12px;
    cursor: pointer;
    transition: border-color 0.15s;
  }
  .filebtn:hover {
    border-color: #a3a3a3;
  }
  .filebtn-tag {
    flex: none;
    color: #a3a3a3;
    font-size: 10px;
    font-weight: 600;
    letter-spacing: 0.05em;
    text-transform: uppercase;
  }
  .filebtn-name {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  /* ---- tracks ---- */
  .tracks {
    display: flex;
    flex-direction: column;
    gap: 6px;
    margin-bottom: 10px;
  }
  .track-row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 6px;
  }
  .track-btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 4px 10px;
    border: 1px solid #525252;
    border-radius: 4px;
    background: #404040;
    color: #f5f5f5;
    font: inherit;
    font-size: 12px;
    font-weight: 500;
    line-height: 1;
    cursor: pointer;
    outline: none;
    transition: border-color 0.15s, opacity 0.15s;
  }
  .track-btn:hover {
    border-color: #a3a3a3;
  }
  .track-btn:focus-visible {
    box-shadow: 0 0 0 2px #a3a3a3;
  }
  .track-btn.off {
    opacity: 0.4;
  }
  .dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
  }
  .dot.off {
    background: transparent;
    box-shadow: inset 0 0 0 2px #737373;
  }

  .inst-wrap {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    color: #a3a3a3;
    font-size: 12px;
  }
  .inst-btn {
    display: inline-flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    width: 200px;
    max-width: 100%;
    padding: 4px 8px;
    border: 1px solid #525252;
    border-left-width: 4px;
    border-radius: 4px;
    background: #404040;
    color: #f5f5f5;
    font: inherit;
    font-size: 12px;
    line-height: 1.25;
    text-align: left;
    cursor: pointer;
    outline: none;
    transition: border-color 0.15s;
  }
  .inst-btn:hover:not(:disabled) {
    border-top-color: #a3a3a3;
    border-right-color: #a3a3a3;
    border-bottom-color: #a3a3a3;
  }
  .inst-btn:focus-visible {
    box-shadow: 0 0 0 2px #737373;
  }
  .inst-btn:disabled {
    cursor: default;
    opacity: 0.4;
  }
  .inst-label {
    min-width: 0;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .chevron {
    flex: none;
    width: 12px;
    height: 12px;
    opacity: 0.6;
  }

  /* ---- piano roll ---- */
  .roll {
    position: relative;
    width: 100%;
    overflow: hidden;
    border: 1px solid #404040;
    border-radius: 4px;
    background: #171717;
    color: #f5f5f5;
  }
  .roll :global(canvas),
  .roll :global(svg) {
    max-width: 100%;
  }
  .roll-empty {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #737373;
    font-size: 14px;
    font-weight: 500;
    pointer-events: none;
  }
  .overlay {
    position: absolute;
    inset: 0;
    z-index: 50;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 8px;
    background: rgba(23, 23, 23, 0.8);
    backdrop-filter: blur(4px);
  }
  .spinner {
    width: 24px;
    height: 24px;
    color: #d4d4d4;
    animation: spin 1s linear infinite;
  }
  .spinner-track {
    opacity: 0.25;
  }
  .spinner-head {
    opacity: 0.75;
  }
  .overlay-text {
    color: #d4d4d4;
    font-size: 12px;
    font-weight: 500;
  }
  @keyframes spin {
    to {
      transform: rotate(360deg);
    }
  }

  /* ---- status ---- */
  .status {
    display: block;
    width: 100%;
    margin-top: 4px;
    padding: 6px 8px;
    border: 1px solid #404040;
    border-radius: 4px;
    background: rgba(38, 38, 38, 0.9);
    color: #d4d4d4;
    font-size: 10px;
    line-height: 1.25;
    text-align: center;
    pointer-events: none;
  }

  /* ---- transport ---- */
  .transport {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 10px;
  }
  .play-btn {
    flex: none;
    display: grid;
    place-items: center;
    width: 34px;
    height: 34px;
    padding: 0;
    border: 0;
    border-radius: 4px;
    background: #e5e5e5;
    color: #171717;
    cursor: pointer;
    outline: none;
    transition: transform 0.1s;
  }
  .play-btn:focus-visible {
    box-shadow: 0 0 0 2px #a3a3a3;
  }
  .play-btn:active {
    transform: scale(0.95);
  }
  .play-btn svg {
    width: 14px;
    height: 14px;
    fill: currentColor;
  }
  .seek {
    flex: 1;
    min-width: 0;
    margin: 0;
    cursor: pointer;
    accent-color: #d4d4d4;
  }
  .time {
    flex: none;
    color: #a3a3a3;
    font-size: 12px;
    font-variant-numeric: tabular-nums;
  }

  /* ---- instrument popover ---- */
  .pop {
    position: fixed;
    z-index: 9999;
    display: flex;
    flex-direction: column;
    min-width: 220px;
    overflow: hidden;
    border: 1px solid #525252;
    border-radius: 8px;
    background: #262626;
    color: #f5f5f5;
    font-family: ui-sans-serif, system-ui, sans-serif;
    box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5);
  }
  .pop-search {
    flex: none;
    width: 100%;
    padding: 8px 10px;
    border: 0;
    border-bottom: 1px solid #525252;
    background: #171717;
    color: #f5f5f5;
    font: inherit;
    font-size: 12px;
    outline: none;
  }
  .pop-search::placeholder {
    color: #737373;
  }
  .pop-list {
    flex: 1;
    margin: 0;
    padding: 4px;
    overflow-y: auto;
    list-style: none;
  }
  .pop-item {
    display: flex;
    align-items: center;
    gap: 6px;
    overflow: hidden;
    padding: 5px 8px;
    border-radius: 4px;
    color: #e5e5e5;
    font-size: 12px;
    white-space: nowrap;
    cursor: pointer;
    transition: background-color 0.15s;
  }
  .pop-item:hover {
    background: #404040;
  }
  .pop-item.active {
    background: #525252;
  }
  .pop-item.current {
    color: #fafafa;
    font-weight: 600;
  }
  .pop-idx {
    flex: none;
    width: 26px;
    color: #737373;
    font-size: 10px;
    font-variant-numeric: tabular-nums;
  }
  .pop-label {
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .pop-empty {
    padding: 5px 8px;
    color: #737373;
    font-size: 12px;
    cursor: default;
  }
</style>