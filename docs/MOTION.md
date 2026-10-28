# Motion setting

The dashboard has two motion paths:

- **Full**: the cinematic loop at display rate with idle animation,
  projectiles, finish sequences, and CSS keyframe pulses.
- **Reduced**: the same stills, rebuilt on a slow repaint beat of
  about 4fps. Static means no animated effects, not no repainting:
  the loop keeps running so the blank-guard probe still fires and
  the stage can never sit black. The overlay reports the real beat
  rate instead of 0fps.

## Precedence

1. `?motion=full` or `?motion=reduced` in the URL wins over
   everything, so a recording never depends on a machine setting.
2. The Motion setting in Settings: Auto, Full, Reduced. Persisted
   in localStorage under `crucible-motion`.
3. Otherwise Auto follows the OS
   `prefers-reduced-motion` media query.

Changing the setting takes effect at once: transients clear and the
stage swaps between the full loop and the slow beat. The classic
arena view reads the same effective value live.
