# Scan Status Feedback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the instant-disappearing scan button with a 3-phase state machine (idle → scanning w/ elapsed timer + shimmer → done) that polls the backend for actual completion and auto-loads results.

**Architecture:** Both Dashboard and TechnicalSetups get the same state machine pattern using `scanPhase` + `elapsed` + `useRef` timer handles. Dashboard polls `/health` for `last_scan` change; TechnicalSetups polls `/technical-setups` for `scan_timestamp` change (with a 15s head-start delay since the scan takes ~45s). A `@keyframes` shimmer animation is added once to `index.css`.

**Tech Stack:** React 18, Vite, inline styles + one global CSS keyframe

---

### Task 1: Add shimmer keyframe to index.css

**Files:**
- Modify: `frontend/src/index.css`

- [ ] **Step 1: Add the keyframe**

Open `frontend/src/index.css` and append at the end:

```css
@keyframes shimmer-sweep {
  0%   { transform: translateX(-100%); }
  100% { transform: translateX(400%); }
}
```

- [ ] **Step 2: Verify dev server accepts it**

Run: `cd frontend && npm run dev`  
Expected: no CSS errors in terminal, server starts on http://localhost:5173

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat: add shimmer-sweep keyframe for scan status animation"
```

---

### Task 2: Update Dashboard.jsx — scan state machine + shimmer

**Files:**
- Modify: `frontend/src/components/Dashboard.jsx:1` (imports)
- Modify: `frontend/src/components/Dashboard.jsx:49-65` (state + runScan)
- Modify: `frontend/src/components/Dashboard.jsx:139-145` (button JSX)
- Modify: `frontend/src/components/Dashboard.jsx:148` (shimmer line insertion)

- [ ] **Step 1: Update imports**

Replace line 1:
```javascript
import { useState } from 'react'
```
With:
```javascript
import { useState, useEffect, useRef } from 'react'
```

Also add `fetchHealth` to the api import (line 6):
```javascript
import { triggerScan, fetchHealth } from '../api.js'
```

- [ ] **Step 2: Replace scanning state + add timer refs**

Replace line 53:
```javascript
const [scanning, setScanning] = useState(false)
```
With:
```javascript
const [scanPhase, setScanPhase] = useState('idle') // 'idle' | 'scanning' | 'done'
const [elapsed, setElapsed] = useState(0)
const pollRef = useRef(null)
const elapsedTimerRef = useRef(null)
const fallbackRef = useRef(null)
```

- [ ] **Step 3: Add clearAllTimers helper and cleanup effect**

Insert after the refs (before the existing `async function runScan()`):
```javascript
function clearAllTimers() {
  clearInterval(pollRef.current)
  clearInterval(elapsedTimerRef.current)
  clearTimeout(fallbackRef.current)
}

useEffect(() => () => clearAllTimers(), [])
```

- [ ] **Step 4: Replace runScan (lines 55–65)**

Replace the entire `runScan` function:
```javascript
async function runScan() {
  const baseline = data?.scan_timestamp ?? null
  setScanPhase('scanning')
  setElapsed(0)

  try {
    await triggerScan()
  } catch (e) {
    setScanPhase('idle')
    return
  }

  elapsedTimerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)

  pollRef.current = setInterval(async () => {
    try {
      const health = await fetchHealth()
      if (health.last_scan && health.last_scan !== baseline) {
        clearAllTimers()
        setScanPhase('done')
        await onRefresh()
        setTimeout(() => setScanPhase('idle'), 2000)
      }
    } catch (_) {}
  }, 3000)

  fallbackRef.current = setTimeout(async () => {
    clearAllTimers()
    setScanPhase('done')
    await onRefresh()
    setTimeout(() => setScanPhase('idle'), 2000)
  }, 60000)
}
```

- [ ] **Step 5: Update the scan button JSX (lines 139–145)**

Replace:
```jsx
<button
  style={{ ...styles.scanBtn, ...(scanning ? styles.scanBtnActive : {}) }}
  onClick={runScan}
  disabled={scanning}
>
  {scanning ? '⟳ SCANNING…' : '▶ RUN SCAN'}
</button>
```
With:
```jsx
<button
  style={{
    ...styles.scanBtn,
    ...(scanPhase === 'scanning' ? styles.scanBtnActive : {}),
    ...(scanPhase === 'done' ? { background: '#00ffaa', color: '#000', opacity: 1 } : {}),
  }}
  onClick={runScan}
  disabled={scanPhase !== 'idle'}
>
  {scanPhase === 'scanning' ? `⟳ SCANNING... ${elapsed}s`
    : scanPhase === 'done' ? '✓ DONE'
    : '▶ RUN SCAN'}
</button>
```

- [ ] **Step 6: Add shimmer line after the closing `</div>` of the header (after line 148)**

After the header closing tag and before the staleness banner block, insert:
```jsx
{scanPhase === 'scanning' && (
  <div style={{ position: 'relative', height: '2px', overflow: 'hidden' }}>
    <div style={{
      position: 'absolute', top: 0, left: 0,
      width: '25%', height: '100%',
      background: 'linear-gradient(90deg, transparent, rgba(0,255,170,0.6), transparent)',
      animation: 'shimmer-sweep 1.5s ease-in-out infinite',
    }} />
  </div>
)}
```

- [ ] **Step 7: Verify in browser**

With the dev server running:
1. Open http://localhost:5173
2. Click "▶ RUN SCAN"
3. Expected: button shows "⟳ SCANNING... 1s", "⟳ SCANNING... 2s", etc. with shimmer line below header
4. After backend scan completes (15–20s): button flashes "✓ DONE", results reload, button returns to "▶ RUN SCAN"

- [ ] **Step 8: Commit**

```bash
git add frontend/src/components/Dashboard.jsx
git commit -m "feat: scan status state machine with elapsed timer and shimmer in Dashboard"
```

---

### Task 3: Update TechnicalSetups.jsx — scan state machine + shimmer, remove 45s banner

**Files:**
- Modify: `frontend/src/components/TechnicalSetups.jsx:1` (imports)
- Modify: `frontend/src/components/TechnicalSetups.jsx:106-144` (state + runScan)
- Modify: `frontend/src/components/TechnicalSetups.jsx:162-176` (button + banner)

- [ ] **Step 1: Update imports**

Replace line 1–2:
```javascript
import { useState, useEffect } from 'react'
import { fetchTechnicalSetups, triggerSetupsScan } from '../api.js'
```
With:
```javascript
import { useState, useEffect, useRef } from 'react'
import { fetchTechnicalSetups, triggerSetupsScan } from '../api.js'
```

- [ ] **Step 2: Replace scanning state + add timer refs**

Replace line 109:
```javascript
const [scanning, setScanning] = useState(false)
```
With:
```javascript
const [scanPhase, setScanPhase] = useState('idle') // 'idle' | 'scanning' | 'done'
const [elapsed, setElapsed] = useState(0)
const pollRef = useRef(null)
const elapsedTimerRef = useRef(null)
const startPollRef = useRef(null)
const fallbackRef = useRef(null)
```

- [ ] **Step 3: Add clearAllTimers helper and cleanup effect**

Insert after the refs (before the existing `useEffect(() => { load() }, [filters])`):
```javascript
function clearAllTimers() {
  clearInterval(pollRef.current)
  clearInterval(elapsedTimerRef.current)
  clearTimeout(startPollRef.current)
  clearTimeout(fallbackRef.current)
}

useEffect(() => () => clearAllTimers(), [])
```

- [ ] **Step 4: Replace runScan (lines 131–144)**

Replace the entire `runScan` function:
```javascript
async function runScan() {
  const baseline = scanTimestamp
  setScanPhase('scanning')
  setElapsed(0)

  try {
    await triggerSetupsScan()
  } catch (e) {
    setError(e.message)
    setScanPhase('idle')
    return
  }

  elapsedTimerRef.current = setInterval(() => setElapsed(e => e + 1), 1000)

  // Wait 15s before polling — scan takes ~45s minimum
  startPollRef.current = setTimeout(() => {
    pollRef.current = setInterval(async () => {
      try {
        const result = await fetchTechnicalSetups(filters)
        if (result.scan_timestamp && result.scan_timestamp !== baseline) {
          clearAllTimers()
          setSetups(result.setups || [])
          setScanTimestamp(result.scan_timestamp)
          setScanPhase('done')
          setTimeout(() => setScanPhase('idle'), 2000)
        }
      } catch (_) {}
    }, 5000)
  }, 15000)

  // Absolute fallback at 90s
  fallbackRef.current = setTimeout(async () => {
    clearAllTimers()
    await load()
    setScanPhase('done')
    setTimeout(() => setScanPhase('idle'), 2000)
  }, 90000)
}
```

- [ ] **Step 5: Update the scan button JSX (lines 162–168)**

Replace:
```jsx
<button
  style={{ ...styles.scanBtn, ...(scanning ? styles.scanBtnActive : {}) }}
  onClick={runScan}
  disabled={scanning}
>
  {scanning ? '⟳ SCANNING…' : '▶ SCAN SETUPS'}
</button>
```
With:
```jsx
<button
  style={{
    ...styles.scanBtn,
    ...(scanPhase === 'scanning' ? styles.scanBtnActive : {}),
    ...(scanPhase === 'done' ? { background: '#00ffaa', color: '#000', opacity: 1 } : {}),
  }}
  onClick={runScan}
  disabled={scanPhase !== 'idle'}
>
  {scanPhase === 'scanning' ? `⟳ SCANNING... ${elapsed}s`
    : scanPhase === 'done' ? '✓ DONE'
    : '▶ SCAN SETUPS'}
</button>
```

- [ ] **Step 6: Remove the 45s banner (lines 172–176)**

Delete this block entirely:
```jsx
{scanning && (
  <div style={styles.scanningBanner}>
    ⟳ Scanning 100 symbols — takes ~45 seconds. Results will load automatically.
  </div>
)}
```

- [ ] **Step 7: Add shimmer line after the header closing tag (line 170)**

After the `</div>` that closes the header block and before the error banner block, insert:
```jsx
{scanPhase === 'scanning' && (
  <div style={{ position: 'relative', height: '2px', overflow: 'hidden' }}>
    <div style={{
      position: 'absolute', top: 0, left: 0,
      width: '25%', height: '100%',
      background: 'linear-gradient(90deg, transparent, rgba(0,255,170,0.6), transparent)',
      animation: 'shimmer-sweep 1.5s ease-in-out infinite',
    }} />
  </div>
)}
```

- [ ] **Step 8: Verify in browser**

1. Click "▶ SCAN SETUPS"
2. Expected: button shows "⟳ SCANNING... 1s", "⟳ SCANNING... 2s", shimmer line visible, old 45s banner is gone
3. After ~45–60s: button flashes "✓ DONE", results reload, button returns to "▶ SCAN SETUPS"

- [ ] **Step 9: Commit**

```bash
git add frontend/src/components/TechnicalSetups.jsx
git commit -m "feat: scan status state machine with elapsed timer and shimmer in TechnicalSetups"
```
