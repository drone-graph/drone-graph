// Atmospheric sound for the mission-control console.
//
// No audio assets: every sound is synthesized via Web Audio. This keeps the
// frontend self-contained and lets us tune the palette in one file. Output
// is intentionally quiet (-18dB or lower) and the ambient hum tracks active-
// drone count, so a silent swarm is silent.
//
// All sounds are atmospheric (Dune / Blade Runner 2049 / Arrival lineage)
// with a few well-chosen responsive accents — per the design brief.

type SoundKind =
  | "prompt" // user sent a prompt
  | "spawn" // drone spawned (subtle pulse)
  | "settle" // gap filled (low bell)
  | "disperse" // drone cancelled or retired (breath out)
  | "dissent" // alignment finding (sub-bass thud)
  | "alert"; // cost ceiling crossed (warmer chime)

let ctx: AudioContext | null = null;
let masterGain: GainNode | null = null;
let humOsc: OscillatorNode | null = null;
let humGain: GainNode | null = null;
let humTargetAmplitude = 0;
let enabled = false;

/**
 * The browser blocks AudioContext until a user gesture. Call this from the
 * first click anywhere. Cheap and idempotent.
 */
export function unlockAudio(): void {
  if (ctx !== null) return;
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    ctx = new Ctx();
    masterGain = ctx.createGain();
    masterGain.gain.value = 0.0;
    masterGain.connect(ctx.destination);
    // Atmospheric hum, off until enabled by the user.
    humOsc = ctx.createOscillator();
    humOsc.type = "sine";
    humOsc.frequency.value = 42; // sub-bass
    humGain = ctx.createGain();
    humGain.gain.value = 0;
    humOsc.connect(humGain);
    humGain.connect(masterGain);
    humOsc.start();
  } catch {
    ctx = null;
  }
}

export function setSoundEnabled(on: boolean): void {
  enabled = on;
  if (masterGain !== null && ctx !== null) {
    const target = on ? 0.7 : 0;
    masterGain.gain.linearRampToValueAtTime(
      target,
      ctx.currentTime + 0.4,
    );
  }
}

export function isSoundEnabled(): boolean {
  return enabled;
}

/**
 * Update ambient hum amplitude from "swarm activity" signal — typically the
 * count of active drones. Smooth ramp; never abrupt.
 */
export function setAmbientActivity(activity: number): void {
  if (humGain === null || ctx === null) return;
  // 0 drones → silence; 1 → -25dB; 4+ → -16dB. Cap on the high side.
  const a = Math.min(4, Math.max(0, activity));
  humTargetAmplitude = a === 0 ? 0 : 0.04 + 0.04 * Math.min(1, a / 4);
  humGain.gain.linearRampToValueAtTime(
    humTargetAmplitude,
    ctx.currentTime + 1.5,
  );
}

/** Play a one-shot sound. Silent if audio is disabled or not yet unlocked. */
export function playSound(kind: SoundKind): void {
  if (!enabled || ctx === null || masterGain === null) return;
  switch (kind) {
    case "prompt":
      pluck(220, 0.18, 0.08);
      break;
    case "spawn":
      pluck(660, 0.06, 0.04);
      break;
    case "settle":
      // Soft bell: two-partial decaying tone.
      pluck(330, 0.45, 0.1);
      setTimeout(() => pluck(495, 0.35, 0.06), 30);
      break;
    case "disperse":
      breathOut(0.6);
      break;
    case "dissent":
      thud(55, 0.35, 0.12);
      break;
    case "alert":
      // Warm chime, copper register.
      pluck(440, 0.5, 0.18);
      setTimeout(() => pluck(523, 0.45, 0.14), 120);
      break;
  }
}

function pluck(freq: number, duration: number, amp: number): void {
  if (ctx === null || masterGain === null) return;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = "sine";
  osc.frequency.value = freq;
  const t0 = ctx.currentTime;
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(amp, t0 + 0.01);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  osc.connect(g);
  g.connect(masterGain);
  osc.start();
  osc.stop(t0 + duration + 0.05);
}

function thud(freq: number, duration: number, amp: number): void {
  if (ctx === null || masterGain === null) return;
  const osc = ctx.createOscillator();
  const g = ctx.createGain();
  osc.type = "sine";
  const t0 = ctx.currentTime;
  osc.frequency.setValueAtTime(freq * 1.4, t0);
  osc.frequency.exponentialRampToValueAtTime(freq, t0 + 0.06);
  g.gain.setValueAtTime(0, t0);
  g.gain.linearRampToValueAtTime(amp, t0 + 0.015);
  g.gain.exponentialRampToValueAtTime(0.0001, t0 + duration);
  osc.connect(g);
  g.connect(masterGain);
  osc.start();
  osc.stop(t0 + duration + 0.05);
}

function breathOut(duration: number): void {
  if (ctx === null || masterGain === null) return;
  // Noise band, low-passed; sounds like an exhale.
  const buffer = ctx.createBuffer(1, ctx.sampleRate * duration, ctx.sampleRate);
  const data = buffer.getChannelData(0);
  for (let i = 0; i < data.length; i++) {
    data[i] = (Math.random() * 2 - 1) * (1 - i / data.length) * 0.4;
  }
  const src = ctx.createBufferSource();
  src.buffer = buffer;
  const lp = ctx.createBiquadFilter();
  lp.type = "lowpass";
  lp.frequency.value = 600;
  const g = ctx.createGain();
  g.gain.value = 0.05;
  src.connect(lp);
  lp.connect(g);
  g.connect(masterGain);
  src.start();
}
