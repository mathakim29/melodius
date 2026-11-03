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

<div class="block overflow-hidden bg-neutral-900 text-neutral-100">
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div
    class="relative overflow-hidden rounded-lg border p-3 font-sans {drag ? 'border-neutral-300' : 'border-neutral-700'} bg-neutral-800"
    ondragenter={onDrag}
    ondragover={onDrag}
    ondragleave={onDragLeave}
    ondrop={onDrop}
  >
    <!-- Top Bar: Conditionally rendered only if midiPicker or sfPicker is enabled -->
    {#if showTopBar}
      <div class="mb-2.5 flex flex-wrap items-center gap-2 pb-1">
        {#if midiPicker}
          <label
            class="inline-flex max-w-[200px] cursor-pointer items-center gap-1.5 overflow-hidden rounded border border-neutral-600 bg-neutral-700 px-2.5 py-1 text-xs text-neutral-100 transition-colors hover:border-neutral-400"
            title="Choose MIDI file (or drop it here)"
          >
            <span class="flex-none text-[10px] font-semibold uppercase tracking-wider text-neutral-400">MIDI</span>
            <span class="min-w-0 truncate" title={midiLabel}>{midiLabel}</span>
            <input class="hidden" type="file" accept=".mid,.midi" onchange={onMidiChange} />
          </label>
        {/if}

        {#if sfPicker && soundfontEnabled}
          <label
            class="inline-flex max-w-[200px] cursor-pointer items-center gap-1.5 overflow-hidden rounded border border-neutral-600 bg-neutral-700 px-2.5 py-1 text-xs text-neutral-100 transition-colors hover:border-neutral-400"
            title="Choose SoundFont file (or drop it here)"
          >
            <span class="flex-none text-[10px] font-semibold uppercase tracking-wider text-neutral-400">SF2</span>
            <span class="min-w-0 truncate" title={sfLabel}>{sfLabel}</span>
            <input class="hidden" type="file" accept=".sf2,.sf3" onchange={onSfChange} />
          </label>
        {/if}
      </div>
    {/if}

    <div class="mb-2.5 flex flex-col gap-1.5">
      {#each Object.entries(tracks) as [key, tr] (key)}
        <div class="flex flex-wrap items-center gap-1.5">
          <button
            type="button"
            class="inline-flex cursor-pointer items-center gap-1.5 rounded border border-neutral-600 bg-neutral-700 px-2.5 py-1 text-xs font-medium leading-none text-neutral-100 outline-none transition hover:border-neutral-400 focus-visible:ring-2 focus-visible:ring-neutral-400 {vis[key] ? '' : 'opacity-40'}"
            aria-pressed={vis[key]}
            onclick={() => toggleVis(key)}
          >
            <span
              class="h-2 w-2 rounded-full"
              class:bg-neutral-300={!vis[key]}
              class:bg-transparent={!vis[key]}
              class:ring-2={!vis[key]}
              class:ring-inset={!vis[key]}
              class:ring-neutral-500={!vis[key]}
              style={vis[key] ? `background-color:${tr.color}` : ''}
            ></span>
            {tr.label}
          </button>

          <span class="inline-flex items-center gap-1.5 text-xs text-neutral-400">
            <span>Instrument</span>
            <button
              type="button"
              class="inline-flex w-[200px] max-w-full cursor-pointer items-center justify-between gap-2 rounded border border-l-4 border-neutral-600 bg-neutral-700 px-2 py-1 text-left text-xs leading-tight text-neutral-100 outline-none transition-colors hover:enabled:border-neutral-400 focus-visible:ring-2 focus-visible:ring-neutral-500 disabled:cursor-default disabled:opacity-40"
              style={`border-left-color:${tr.color}`}
              aria-haspopup="listbox"
              disabled={!midiPicker && !sfPicker}
              title={instLabel(tr)}
              onclick={(e) => onPickClick(e, key)}
            >
              <span class="min-w-0 truncate">{instLabel(tr)}</span>
              <svg class="h-3 w-3 flex-none opacity-60" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9" /></svg>
            </button>
          </span>
        </div>
      {/each}
    </div>

    <!-- Piano roll container with loading overlay and fallback text -->
    <div
      class="relative w-full overflow-hidden rounded border border-neutral-700 bg-neutral-900 text-neutral-100 [&_canvas]:max-w-full [&_svg]:max-w-full"
      bind:this={rollEl}
      style="height:{height}px"
    >
      {#if notes.length === 0 && !isLoading}
        <div class="absolute inset-0 flex items-center justify-center pointer-events-none text-neutral-500 text-sm font-medium">
          Midi not loaded yet
        </div>
      {/if}

      {#if isLoading}
        <div class="absolute inset-0 z-50 flex flex-col items-center justify-center bg-neutral-900/80 backdrop-blur-xs gap-2">
          <svg class="animate-spin h-6 w-6 text-neutral-300" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
          </svg>
          <span class="text-xs font-medium text-neutral-300">{loadingMessage}</span>
        </div>
      {/if}
    </div>

    {#if status}
      <div
        class="pointer-events-none mt-1 block w-full rounded border border-neutral-700 bg-neutral-800/90 px-2 py-1.5 text-center text-[10px] leading-tight text-neutral-300"
        role="status"
        aria-live="polite"
      >{status}</div>
    {/if}

    <div class="mt-2.5 flex items-center gap-2.5">
      <button
        class="grid h-[34px] w-[34px] flex-none cursor-pointer place-items-center rounded border-0 bg-neutral-200 p-0 text-neutral-900 outline-none transition-transform focus-visible:ring-2 focus-visible:ring-neutral-400 active:scale-95"
        aria-label={playing ? 'Pause' : 'Play'}
        onclick={() => toggle()}
      >
        {#if playing}
          <svg class="h-3.5 w-3.5 fill-current" viewBox="0 0 24 24"><path d="M6 5h4v14H6zM14 5h4v14h-4z" /></svg>
        {:else}
          <svg class="h-3.5 w-3.5 fill-current" viewBox="0 0 24 24"><path d="M8 5v14l11-7z" /></svg>
        {/if}
      </button>
      <input
        class="m-0 min-w-0 flex-1 cursor-pointer accent-neutral-300"
        type="range"
        min="0"
        max="1000"
        value={seekValue}
        oninput={(e) => { seekValue = +e.currentTarget.value; }}
        onchange={onSeek}
      />
      <span class="flex-none text-xs tabular-nums text-neutral-400">{timeText}</span>
    </div>
  </div>

  {#if pop}
    <div
      class="fixed z-[9999] flex min-w-[220px] flex-col overflow-hidden rounded-lg border border-neutral-600 bg-neutral-800 font-sans text-neutral-100 shadow-2xl"
      style={pop.style}
    >
      <input
        class="w-full flex-none border-0 border-b border-neutral-600 bg-neutral-900 px-2.5 py-2 text-xs text-neutral-100 outline-none placeholder:text-neutral-500"
        type="text"
        placeholder="Search instruments…"
        spellcheck="false"
        autocomplete="off"
        bind:this={popSearchEl}
        bind:value={popQuery}
        oninput={() => (popIdx = 0)}
        onkeydown={onPopKey}
      />
      <ul class="m-0 flex-1 list-none overflow-y-auto p-1" role="listbox" bind:this={popListEl} style="max-height:{pop.listMax}px">
        {#each filtered as it, i (it.n)}
          <!-- svelte-ignore a11y_click_events_have_key_events -->
          <li
            class="flex cursor-pointer items-center gap-1.5 overflow-hidden whitespace-nowrap rounded px-2 py-[5px] text-xs transition-colors {i === popIdx ? 'bg-neutral-600' : 'hover:bg-neutral-700'} {it.value === pop.current ? 'font-semibold text-neutral-50' : 'text-neutral-200'}"
            role="option"
            aria-selected={it.value === pop.current}
            title={it.label}
            onpointermove={() => { if (popIdx !== i) popIdx = i; }}
            onclick={() => commitPicker(i)}
          >
            <span class="w-[26px] flex-none text-[10px] tabular-nums text-neutral-500">{it.value === null ? '' : it.n}</span>
            <span class="truncate">{it.label}</span>
          </li>
        {:else}
          <li class="cursor-default px-2 py-[5px] text-xs text-neutral-500">No matches</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>
