<script>
  import { onMount } from 'svelte';
  import Toastify from 'toastify-js';
  import 'toastify-js/src/toastify.css';
  import PianoRollPlayer from './PianoRollPlayer.svelte';

  const API_BASE = 'http://127.0.0.1:8000';
  let player;
  let sourceUrl = $state('');
  let midiUrl = $state('');
  let busy = $state(false);
  let apiOnline = $state(false);
  let checkingApi = $state(true);

  function toast(message, type = 'error') {
    Toastify({
      text: message,
      duration: 4500,
      gravity: 'bottom',
      position: 'right',
      close: true,
      style: type === 'error'
        ? { background: '#27272a', border: '1px solid #71717a' }
        : { background: '#f4f4f5', color: '#18181b', border: '1px solid #a1a1aa' }
    }).showToast();
  }

  async function checkApi() {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 2500);
    try {
      const response = await fetch(`${API_BASE}/health`, { signal: controller.signal });
      const wasOffline = !apiOnline;
      apiOnline = response.ok;
      if (response.ok && wasOffline && !checkingApi) toast('API reconnected', 'success');
    } catch {
      apiOnline = false;
    } finally {
      clearTimeout(timeout);
      checkingApi = false;
    }
  }

  async function transcribe() {
    if (!sourceUrl.trim()) return toast('Enter an audio or video URL.');
    if (!apiOnline) return toast('The API is offline. Waiting for it to reconnect.');
    busy = true;
    midiUrl = '';
    try {
      const body = new FormData();
      body.append('url', sourceUrl.trim());
      const response = await fetch(`${API_BASE}/transcribe`, { method: 'POST', body });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'Transcription failed');
      midiUrl = result.file.startsWith('http') ? result.file : `${API_BASE}${result.file}`;
      toast('MIDI ready', 'success');
    } catch (error) {
      toast(error.message || 'Could not reach the API.');
    } finally {
      busy = false;
    }
  }

  onMount(() => {
    checkApi();
    const interval = setInterval(checkApi, 3000);
    return () => clearInterval(interval);
  });
</script>

<svelte:head>
  <title>Melodius</title>
  <meta name="description" content="Turn songs into playable MIDI piano rolls" />
</svelte:head>

<main class="min-h-screen px-4 py-12 text-zinc-100 mx-auto max-w-3xl">

  <div class="flex items-center justify-center mb-10">
  <img src="logo.png">
  </div>

  <section class="bg-zinc-900 p-10">
    <!-- <header class="mb-8">
      <div class="flex items-center justify-between">
        <p class="font-mono text-xs uppercase tracking-[0.3em] text-zinc-300">melodius</p>
        <span class="flex items-center gap-2 font-mono text-[10px] uppercase tracking-widest text-zinc-500">
          <span class:animate-pulse={!apiOnline} class={`h-2 w-2 rounded-full ${apiOnline ? 'bg-zinc-100' : 'bg-zinc-600'}`}></span>
          {checkingApi ? 'connecting' : apiOnline ? 'api online' : 'api offline'}
        </span>
      </div>
      <h1 class="mt-3 text-3xl font-semibold tracking-tight">Hear the melody</h1>
      <p class="mt-2 text-sm text-zinc-400">Paste a song URL and turn its melody into a playable piano roll.</p>
    </header> -->

    <form onsubmit={(event) => { event.preventDefault(); transcribe(); }} class="space-y-5">
      <label class="block space-y-2">
        <span class="text-sm font-medium">Audio or video URL</span>
        <input bind:value={sourceUrl} type="url" required placeholder="https://..." class="w-full rounded-lg border border-zinc-700 bg-zinc-950 px-3 py-2 text-sm outline-none ring-zinc-300 focus:ring-2" />
      </label>
      <button type="submit" disabled={busy || !apiOnline} class="w-full rounded-lg bg-zinc-100 px-4 py-2.5 text-sm font-semibold text-zinc-950 transition hover:bg-white disabled:cursor-not-allowed disabled:opacity-50">
        {busy ? 'Transcribing...' : !apiOnline ? 'Waiting for API...' : 'Transcribe melody'}
      </button>
    </form>

    {#if midiUrl}
      <section class="mt-6">
        <div class="mb-4 flex items-center justify-between">
          <h2 class="font-medium">Piano roll</h2>
          <a href={midiUrl} download class="text-xs text-zinc-300 hover:text-white">Download MIDI</a>
        </div>
        <div class="overflow-hidden rounded-lg border border-zinc-800 bg-black">
          <PianoRollPlayer bind:this={player} midi={midiUrl} soundfont={true} sfPicker={false} midiPicker={false} height={320} onerror={(event) => toast(event.message || 'Piano roll error')} />
        </div>
      </section>
    {/if}
  </section>
</main>

<style>
  @import "tailwindcss";

  :global(body) {
  position: relative;
  margin: 0;
  min-height: 100vh;
}

:global(body::before) {
  content: "";
  position: fixed;
  top: 0;
  left: 0;
  width: 100%;
  height: 100%;
  
  /* Image fit settings */
  background-image: url("/bg.jpg");
  background-size: cover;
  background-position: center;
  background-repeat: no-repeat;
  
  /* Transparency and layering */
  opacity: 0.2; /* Adjust from 0.0 (invisible) to 1.0 (solid) */
  z-index: -1;
  filter: grayscale(100%);
}

</style>
