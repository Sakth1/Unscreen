# Post-Update Debugging: Important Findings

## Summary

Debugged and fixed two post-update bugs across 4 dev releases (dev5→dev8):
1. **Windows**: Empty/blank window after post-update relaunch
2. **Android**: Duplicate instances when "Open" pressed in package installer after update

---

## Windows: Blank Screen After Post-Update Relaunch

### Root Cause
**Flet issue #6101** — `page.window.maximized = True` fails sporadically (4/10 times) and shows a blank screen. The Dart client hasn't fully settled when the maximize operation fires, causing a race that blanks the rendered content.

### Evidence
- Startup.log showed all steps succeed (mutex, config, page.add)
- The app eventually works after manual close+reopen
- The blank window only occurs on the first launch after update

### Fix
Increased `_maximize_after_delay` from 0.1s to 2.0s (`src/app.py:345-350`).

The 0.1s delay was insufficient for the flet Dart client to fully initialize on a cold post-update start. The 2.0s delay matches the Android post-update delay and gives the client time to settle.

### Flet Issue Reference
- https://github.com/flet-dev/flet/issues/6101
- "Maximizing the window with page.window.maximized = True fails sporadically in my experience 4/10 times it fails. It shows then a windowed app with either a blank screen or with the string 'working'"

---

## Android: Duplicate Instances After Update

### Root Cause 1: `activity.finish()` doesn't remove from recents
`activity.finish()` only finishes the current activity. Android keeps the task in recents (standard behavior). The user sees the old task in recents, and tapping it shows a dead activity.

### Root Cause 2: `page.window.destroy()` blocks indefinitely
After `activity.finish()` succeeds, the flet bridge is dead. `page.window.destroy()` waits for a response from the dead bridge and blocks indefinitely. The 10-second timeout is not enforced on Android. `os._exit(0)` is never reached, leaving zombie processes.

### Root Cause 3: Duplicate detection false positive (same PID)
Flet restarts the Dart VM within the **same OS process** (same PID). The lock file contained the current process's own PID, causing `_check_android_duplicate()` to think it was a duplicate and exit.

### Evidence from Logs
```
18:08:15,227 - calling activity.finish() directly
18:08:15,241 - activity.finish() succeeded
18:08:15,242 - calling page.window.destroy()
18:08:15,468 - dart_bridge.send_bytes failed (bridge dead)
18:08:28 to 18:08:38 - multiple Dart_PostCObject_DL failed errors (blocked)
```

```
18:09:17,557 - Duplicate detection: old_pid=24347 still running, current_pid=24347
18:09:17,557 - Android duplicate instance detected... Exiting
```

### Fix
1. Replaced `activity.finish()` with `activity.finishAndRemoveTask()` (API 21+)
2. Removed `page.window.destroy()` — call `os._exit(0)` immediately after activity cleanup
3. Added `old_pid == current_pid` check — if PIDs match, it's a Dart VM restart, not a duplicate

---

## Key Learnings

### Flet on Android
1. **`page.window.destroy()` is unreliable** — blocks indefinitely when the bridge is dead. Use `activity.finish()` or `finishAndRemoveTask()` via jnius instead.
2. **Dart VM restarts reuse the same OS process** — PID doesn't change. Don't use PID-based duplicate detection.
3. **`activity.finish()` doesn't remove from recents** — use `finishAndRemoveTask()` instead.
4. **`Dart_PostCObject_DL failed` errors** — occur when the Python process tries to send messages through a dead bridge. Not harmful but noisy.

### Flet on Windows
1. **`page.window.maximized = True` is fragile** — fails sporadically (4/10 times per flet #6101). The Dart client needs time to settle before maximize.
2. **Workaround**: Delay maximize by 2.0s minimum. The code comment says "REMOVE THIS BS OF A CODE WHEN flet #6101 IS FIXED".

### Inno Setup Watchdog
1. **Watchdog polls for both PIDs** — the setup process and the old app process
2. **3-second delay after both die** — may not be enough on slow systems
3. **Mutex race** — if the old process is still alive when the watchdog launches the new app, the mutex blocks the new instance

---

## PRs in This Series
- **#95** (dev5): Post-update diagnostic logging
- **#96** (dev6): Android activity.finish() + 3-tier cleanup
- **#97** (dev7): Lock-file duplicate detection + destroyed session guard
- **#98** (dev8): Windows blank screen fix + Android finishAndRemoveTask + duplicate PID fix
- **#99** (dev9): Fresh Android activity resolution + Windows post-update maximize skip

---

## Dev8→Dev9: Why dev9 is needed

### Android: Stale cached activity

Dev8's `finishAndRemoveTask()` fix lives in the **old** binary (the one that fires the installer). But `get_activity()` caches the `mActivity` reference at startup. After a Dart VM restart, flet creates a **new** `mActivity` (different JNI object, different address). Calling `finishAndRemoveTask()` on the stale cached activity is a no-op — the old task stays in recents.

**Evidence from dev7→dev8 log:**
```
18:08:15,227 - calling activity.finish() directly (activity=<android.app.Activity at 0x761663f130 ...>)
18:09:14,687 - get_activity: resolved activity=<android.app.Activity at 0x761478b130 ...>
```

The cached `0x761663f130` is stale after the Dart VM restart. The new activity is `0x761478b130`.

**Fix (dev9):** `_close()` resolves a **fresh** `mActivity` via `autoclass(host_class).mActivity` instead of using the cached `get_activity()`. Logs `isFinishing()` and `taskId` for diagnostic verification.

### Windows: Post-update maximize race

Dev8 increased `_maximize_after_delay` from 0.1s to 2.0s, but the blank screen persists. The issue is probabilistic (flet #6101) and 2.0s reduces but doesn't eliminate it.

**Fix (dev9):** Watchdog CMD sets `UNSCREEN_POST_UPDATE=1` env before launching the app. `App.__init__` checks this env var and skips `_schedule_maximize()` entirely on post-update relaunch, avoiding the race completely.

---

## Dev7→Dev8 Log Analysis (from user test)

### Android
- `finishAndRemoveTask()` was NOT called (log shows `activity.finish()` — this is dev7's old binary)
- `page.window.destroy()` blocked indefinitely (Dart bridge dead)
- Duplicate detection: `old_pid=24347 == current_pid=24347` → `Exiting` (dev7's old binary, not dev8's fix)
- Old instance lingers in recents with frozen UI (bridge dead, process alive)

### Windows
- All startup steps succeed (mutex, config, page.add)
- Title bar renders but content is empty (flet #6101 race, not Python error)
- `start_maximized` triggered maximize, which blanked the content
