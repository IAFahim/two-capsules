/* Gapless playlist — no hiccups.
 *
 * Strategy (deliberately dumb and solid):
 *   1. Decode every clip in the list BEFORE any sound starts.
 *   2. Schedule every BufferSource on the AudioContext clock back-to-back
 *      in one shot. Clip N+1.start = clip N.start + clip N.duration.
 *   3. Never re-schedule mid-play. Never await fetch while audio is running.
 *
 * That eliminates the classic HTML5 ended→play gap AND the previous
 * "look-ahead horizon" underruns that felt like hiccups.
 */
(function (global) {
  "use strict";

  function create(opts) {
    opts = opts || {};
    let ctx = null;
    let urls = [];
    let bufs = [];          // AudioBuffer | null
    let sources = [];
    let timers = [];
    let playing = false;
    let index = -1;
    let epoch = 0;
    let rate = 1;

    function ac() {
      if (!ctx) {
        const AC = global.AudioContext || global.webkitAudioContext;
        if (!AC) throw new Error("Web Audio not supported");
        ctx = new AC();
      }
      if (ctx.state === "suspended") {
        // resume() returns a promise; fire-and-forget is fine after a gesture
        ctx.resume();
      }
      return ctx;
    }

    function getRate() {
      try {
        const r = (opts.rate && opts.rate()) || rate || 1;
        return r > 0 ? r : 1;
      } catch (_) {
        return 1;
      }
    }

    function clearTimers() {
      for (const t of timers) clearTimeout(t);
      timers = [];
    }

    function killSources() {
      for (const s of sources) {
        try { s.onended = null; } catch (_) {}
        try { s.stop(0); } catch (_) {}
        try { s.disconnect(); } catch (_) {}
      }
      sources = [];
    }

    function setUrls(list) {
      stop();
      urls = (list || []).slice();
      bufs = urls.map(function () { return undefined; });
      index = -1;
    }

    async function decodeOne(i) {
      if (bufs[i] !== undefined) return bufs[i];
      const u = urls[i];
      if (!u) {
        bufs[i] = null;
        return null;
      }
      try {
        const res = await fetch(u, { cache: "force-cache" });
        if (!res.ok) throw new Error("HTTP " + res.status + " " + u);
        const ab = await res.arrayBuffer();
        // copy so decodeAudioData can take ownership
        const copy = ab.slice(0);
        const buf = await ac().decodeAudioData(copy);
        bufs[i] = buf;
        return buf;
      } catch (e) {
        bufs[i] = null;
        if (opts.onError) opts.onError(e, i);
        return null;
      }
    }

    /** Decode every clip. Call before play for zero underruns. */
    async function warm() {
      ac();
      // parallel decode — chapters are small (dozens of short opus files)
      await Promise.all(urls.map(function (_, i) { return decodeOne(i); }));
    }

    function stop() {
      epoch++;
      playing = false;
      clearTimers();
      killSources();
      index = -1;
    }

    /**
     * Play from index `from` (default 0).
     * Always warms the whole list first so scheduling never waits on network.
     */
    async function play(from) {
      from = from == null ? 0 : from | 0;
      if (from < 0) from = 0;

      const my = ++epoch;
      playing = false;
      clearTimers();
      killSources();

      // unlock / create context on the user gesture that called play()
      const c = ac();
      try { await c.resume(); } catch (_) {}

      if (opts.onLoading) opts.onLoading(true);
      await warm();
      if (my !== epoch) return; // superseded
      if (opts.onLoading) opts.onLoading(false);

      // skip leading empties
      while (from < urls.length && !bufs[from]) from++;
      if (from >= urls.length) {
        playing = false;
        if (opts.onDone) opts.onDone();
        return;
      }

      const r = getRate();
      // small lead-in so the first start isn't in the past after decode work
      let t = c.currentTime + 0.05;
      let lastSrc = null;
      let lastI = -1;

      for (let i = from; i < urls.length; i++) {
        const buf = bufs[i];
        if (!buf) continue; // missing = hard skip, no silence

        const src = c.createBufferSource();
        src.buffer = buf;
        src.playbackRate.value = r;
        src.connect(c.destination);

        // start(when) — when is in AudioContext time, sample-accurate
        src.start(t);

        const captureI = i;
        const startAt = t;
        const wallDelay = Math.max(0, (startAt - c.currentTime) * 1000);
        timers.push(setTimeout(function () {
          if (my !== epoch || !playing) return;
          index = captureI;
          if (opts.onIndex) opts.onIndex(captureI);
        }, wallDelay));

        lastSrc = src;
        lastI = i;
        sources.push(src);
        // duration at this playbackRate
        t += buf.duration / r;
      }

      if (!lastSrc) {
        playing = false;
        if (opts.onDone) opts.onDone();
        return;
      }

      playing = true;
      // fire onIndex for the first clip immediately (don't wait for timer jitter)
      index = from;
      while (index < urls.length && !bufs[index]) index++;
      if (opts.onIndex) opts.onIndex(index);

      lastSrc.onended = function () {
        if (my !== epoch) return;
        // only the true last source ends the playlist
        if (playing) {
          playing = false;
          if (opts.onDone) opts.onDone();
        }
      };
    }

    async function seek(i) {
      await play(i);
    }

    function setRate(r) {
      rate = r;
      // Mid-play rate change: reschedule from current index (clean, no drift math)
      if (playing && index >= 0) {
        const i = index;
        play(i);
      }
    }

    function currentIndex() { return index; }
    function isPlaying() { return playing; }

    /** Warm AudioContext on a user gesture without playing. */
    function unlock() {
      try {
        const c = ac();
        c.resume();
      } catch (_) {}
    }

    return {
      setUrls: setUrls,
      play: play,
      seek: seek,
      stop: stop,
      setRate: setRate,
      warm: warm,
      unlock: unlock,
      currentIndex: currentIndex,
      isPlaying: isPlaying,
    };
  }

  global.Gapless = { create: create };
})(typeof window !== "undefined" ? window : globalThis);
