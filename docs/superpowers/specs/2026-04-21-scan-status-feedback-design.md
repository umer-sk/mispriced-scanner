# Scan Status Feedback — Design Spec

**Date:** 2026-04-21  
**Scope:** Frontend only (Dashboard.jsx, TechnicalSetups.jsx) — no backend changes

## Problem

Both scan buttons (`▶ RUN SCAN` on Scanner tab, `▶ SCAN SETUPS` on Setups tab) fire a background task on the backend and return immediately. The current UI shows "⟳ SCANNING…" only until the HTTP response comes back (~200ms), then snaps back to idle. The user has no feedback that the backend is still running for the next 15–60 seconds.

## Solution

Button + Shimmer pattern: the scan button itself cycles through states, with an animated shimmer line as a secondary activity indicator. No new UI elements. Auto-loads results on completion.

## Button States

Both tabs use the same three-state cycle:

| State | Button appearance | Interaction |
|---|---|---|
| **Idle** | `▶ RUN SCAN` / `▶ SCAN SETUPS` — solid green, filled | Clickable |
| **Scanning** | `⟳ SCANNING... Xs` — ghost/outlined, elapsed seconds increment every 1s | Disabled |
| **Complete** | `✓ DONE` — solid green flash, 2 seconds | Disabled, then auto-transitions to idle |

Elapsed seconds counter starts from 0 when scan is triggered and updates via `setInterval(1000)`.

## Shimmer Line

A 2px animated line sits at the bottom edge of the header row (below the button row, above the content area). It sweeps left-to-right repeatedly using a CSS gradient animation while the scan is in the `scanning` state. Color: `#00ffaa` at 50% opacity. Disappears when scan completes.

```css
@keyframes shimmer {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(100%); }
}
```

## Completion Detection

Neither scan endpoint reports completion — both return immediately with `{"status": "scan started"}`. Detection is done by polling for a changed timestamp.

### Scanner Tab

- On trigger: capture current `scan_timestamp` from the last `/opportunities` response (already in component state)
- Poll `/health` every 3 seconds after triggering
- Completion condition: `last_scan` in health response differs from the captured baseline
- Fallback: force-complete at 60 seconds

### Setups Tab

- On trigger: capture current `scan_timestamp` from the last `/technical-setups` response
- Start polling `/technical-setups` after a 15-second delay (scan takes ~45s minimum)
- Poll every 5 seconds
- Completion condition: `scan_timestamp` in response differs from the captured baseline
- Fallback: force-complete at 90 seconds

Both use `useRef` for the baseline timestamp (not state, to avoid re-renders) and clear all intervals/timers on unmount.

## On Completion

1. Transition button to `✓ DONE` state
2. Immediately call the existing load/refresh function to fetch new results
3. After 2 seconds, transition button back to idle

Old results remain visible and usable during the scan — no clearing, no loading overlay.

## Removes

The existing hardcoded 45-second "⟳ Scanning 100 symbols…" blue banner in `TechnicalSetups.jsx` is removed. The button elapsed timer replaces it entirely.

## Files Changed

| File | Change |
|---|---|
| `frontend/src/components/Dashboard.jsx` | Button state machine, shimmer line, polling logic |
| `frontend/src/components/TechnicalSetups.jsx` | Button state machine, shimmer line, polling logic, remove 45s banner |

## Out of Scope

- Backend progress endpoint (no `/scan-status` route)
- Sector analysis refresh indicator
- Auto-refresh interval changes
