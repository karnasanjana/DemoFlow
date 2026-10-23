"""Playwright: log in, execute parsed actions, and record the session video.

Mode A recording — the browser session is captured continuously via the
context's record_video option while actions execute, so the output shows
real product behavior. The whole thing is fully automatic: after the user
clicks Start, authentication runs first (if configured), then every parsed
instruction is executed without any manual interaction. Failed actions are
logged and skipped — a partial recording beats no recording.

GCP MIGRATION: run in a Cloud Run container built from the Playwright image.
"""

import asyncio
import base64
import json
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import Error as PlaywrightError, Page, async_playwright

from core.config import settings
from core.progress import ProgressBus
from core.storage import auth_state_path, flow_dir, log_error, recording_dir
from models.flow import AuthConfig, AuthMethod, ParsedAction
from services.ffmpeg import run_ffmpeg
from services.gemini import extract_json, generate, has_gemini

# A normal, current desktop-Chrome user-agent. Identity providers (Google,
# Microsoft, …) block sign-in on browsers that look automated ("This browser or
# app may not be secure"); a realistic UA is one part of looking normal.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# Runs before any page script: hides the navigator.webdriver flag that marks the
# session as automation-driven (the other half of the "may not be secure" block).
_STEALTH_SCRIPT = (
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
)

# How long manual sign-in may block before we give up and continue anyway.
_MANUAL_LOGIN_TIMEOUT_S = 5 * 60

# Stable launch flags. We deliberately keep the bundled Chromium (no channel=
# "chrome": that competes with the user's own Chrome profile and is flaky) and
# NEVER pass --no-sandbox (that weakens the browser sandbox). The one tweak is
# disabling the AutomationControlled blink feature, which is safe and lets a
# human sign in without the "this browser may not be secure" block.
_LAUNCH_ARGS = ["--disable-blink-features=AutomationControlled"]


async def _launch_context(p, *, viewport_width: int, viewport_height: int,
                          rec_dir: Path, headless: bool, slow_mo: int = 0,
                          storage_state: Path | None = None,
                          no_viewport: bool = False):
    """Launch a normal (non-persistent) browser + recording context.

    This is the Playwright-recommended setup for automated testing: a clean
    Chromium with the sign-in saved as `storage_state` (cookies + localStorage)
    and replayed on later runs, instead of a persistent on-disk profile. Returns
    (browser, context); the caller closes both.

    `no_viewport` disables Playwright's fixed-viewport device-metrics emulation so
    the page uses the real OS window. Manual mode needs this: with an emulated
    viewport, headful Chromium ignores the user's mouse-wheel/trackpad and the
    page won't scroll. Automated mode keeps the fixed viewport (it scrolls via
    page.mouse.wheel, and consistent viewport px keep click anchoring accurate)."""
    # Size the OS window to fully contain the viewport (+ room for the toolbar).
    # Without this, headful Chromium renders the area below the real window as a
    # grey unpainted band in the recorded video.
    args = _LAUNCH_ARGS + [
        f"--window-size={viewport_width},{viewport_height + 140}",
        "--window-position=0,0",
    ]
    browser = await p.chromium.launch(headless=headless, slow_mo=slow_mo, args=args)
    ctx_opts = dict(
        record_video_dir=str(rec_dir),
        record_video_size={"width": viewport_width, "height": viewport_height},
        ignore_https_errors=True,
        user_agent=_USER_AGENT,
    )
    if no_viewport:
        ctx_opts["no_viewport"] = True   # use the real window → physical scrolling works
    else:
        ctx_opts["viewport"] = {"width": viewport_width, "height": viewport_height}
    use_state = storage_state is not None and storage_state.exists()
    try:
        context = await browser.new_context(
            storage_state=str(storage_state) if use_state else None, **ctx_opts
        )
    except Exception:
        # A corrupt/partial saved session must not break every future run —
        # fall back to a clean context (the user will just sign in again).
        context = await browser.new_context(**ctx_opts)
    return browser, context


async def _close_quietly(context, browser, flow_id: str) -> None:
    """Finalize the recording: close the context (flushes the .webm) then the
    browser. Tolerates a window the user already closed / a crashed browser —
    the .webm is usually still on disk, so we keep going and salvage it."""
    try:
        await context.close()  # always finalize the .webm, even if actions failed
    except Exception as exc:
        log_error(flow_id, "recorder: context.close failed (browser already closed?)", exc)
    try:
        await browser.close()
    except Exception as exc:
        log_error(flow_id, "recorder: browser.close failed (browser already closed?)", exc)


# ---------------------------------------------------------------------------
# Low-level element helpers
# ---------------------------------------------------------------------------


async def _locator_center(locator) -> tuple[float, float] | None:
    """Center point (viewport px) of a locator, for anchoring the click animation."""
    try:
        box = await locator.bounding_box()
        if box:
            return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    except Exception:
        pass
    return None


async def smart_click(page: Page, description: str) -> tuple[float, float]:
    """Gemini Vision fallback: screenshot → element coordinates → click. Returns
    the (x, y) pixel point that was clicked."""
    if not has_gemini():
        raise RuntimeError("element not found and no GEMINI_API_KEY for vision fallback")
    screenshot = await page.screenshot()
    b64 = base64.b64encode(screenshot).decode()
    prompt = (
        f'Look at this screenshot. Find the UI element described as: "{description}"\n'
        'Return ONLY a JSON object: {"x": number, "y": number} with the pixel '
        "coordinates to click. Center of the element."
    )
    response = await generate([{"inline_data": {"mime_type": "image/png", "data": b64}}, prompt])
    coords = extract_json(response)
    x, y = float(coords["x"]), float(coords["y"])
    await page.mouse.click(x, y)
    try:
        await page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        pass
    return x, y


async def _click(page: Page, action: ParsedAction, flow_id: str) -> tuple[float, float] | None:
    """Try role=button → visible text → CSS selector → Gemini Vision. Never raises.

    Returns the click point (viewport px) so the demo can anchor its highlight
    animation exactly where the click landed, or None if nothing was clicked."""
    target = (action.selector or action.description or "").strip()
    selector = (action.selector or "").strip()
    strategies = []
    if target:
        strategies.append(page.get_by_role("button", name=target, exact=False).first)
        strategies.append(page.get_by_role("link", name=target, exact=False).first)
        strategies.append(page.get_by_text(target, exact=False).first)
    if selector:
        strategies.append(page.locator(selector).first)
    last_error: Exception | None = None
    for locator in strategies:
        try:
            await locator.click(timeout=5000)
            return await _locator_center(locator)
        except Exception as exc:
            last_error = exc
    try:
        return await smart_click(page, action.description or target)
    except Exception as exc:
        log_error(flow_id, f"recorder: could not click '{target}'", last_error or exc)
        return None


async def _type(page: Page, action: ParsedAction, flow_id: str) -> bool:
    """Try fill via selector/placeholder/label → first textbox → click+keyboard. Never raises."""
    selector = (action.selector or "").strip()
    text = action.text or ""
    candidates = []
    if selector:
        candidates.append(page.locator(selector).first)
        candidates.append(page.get_by_placeholder(selector).first)
        candidates.append(page.get_by_label(selector).first)
    candidates.append(page.get_by_role("searchbox").first)
    candidates.append(page.get_by_role("combobox").first)  # e.g. Google's search field
    candidates.append(page.get_by_role("textbox").first)

    for locator in candidates:
        try:
            await locator.fill(text, timeout=5000)
            return True
        except Exception:
            continue
    # Fallback: focus the field by clicking, then type with the keyboard.
    for locator in candidates:
        try:
            await locator.click(timeout=3000)
            await page.keyboard.type(text, delay=15)
            return True
        except Exception:
            continue
    log_error(flow_id, f"recorder: could not type into '{selector}'", "no matching field found")
    return False


async def _navigate(page: Page, url: str) -> None:
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
    except Exception:
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)
        await page.wait_for_timeout(800)


async def _settle(page: Page) -> None:
    """Let navigation and animations finish after an interaction. Returns as soon as
    the network is idle; the short fixed wait just covers CSS transitions."""
    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass
    await asyncio.sleep(settings.record_settle_s)


# ---------------------------------------------------------------------------
# Authentication — runs once, before any instruction
# ---------------------------------------------------------------------------


# Fields that mean a sign-in form is on screen. Covers single-page forms and the
# two-step flows of major providers (Google asks for the email first, then the
# password on a separate screen — so checking only for a password field misses
# the first step). If none of these are visible we treat the user as signed in.
_LOGIN_FIELD_SELECTORS = (
    "input[type=password], input[type=email], "
    "input[name=identifier], input[name=loginfmt], "  # Google / Microsoft
    "input[autocomplete=username]"
)


async def _login_field_visible(page: Page) -> bool:
    """True if a sign-in field is currently shown — our proxy for 'not logged in
    yet'. Used both to decide whether to pause for sign-in and to auto-detect
    when the user has finished (the form disappears after a successful login)."""
    try:
        return await page.locator(_LOGIN_FIELD_SELECTORS).first.is_visible()
    except Exception:
        return False


def _registrable(host: str) -> str:
    """Last two labels of a hostname — 'accounts.google.com' → 'google.com',
    'app.example.com' → 'example.com'. Good enough to tell whether we're back on
    the product's own site versus off on an identity provider."""
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def _is_auth_host(host: str) -> bool:
    """True for hostnames that belong to a sign-in / identity provider, so we
    never mistake a mid-login page (e.g. accounts.google.com between the email
    and password steps) for 'done'."""
    host = (host or "").lower()
    known = ("accounts.google.com", "login.microsoftonline.com", "login.live.com",
             "appleid.apple.com")
    if any(host == h or host.endswith("." + h) for h in known):
        return True
    return host.split(".")[0] in {"login", "signin", "auth", "accounts", "sso", "oauth"}


def _logged_in_on(page: Page, target_host: str) -> bool:
    """We treat sign-in as finished only once the browser is back on the
    product's own site (not an identity provider). This is what prevents the
    auto-continue from firing during the brief field-less moment between Google's
    email and password steps — there the host is accounts.google.com, not the
    target site, so we keep waiting."""
    host = (urlparse(page.url).hostname or "").lower()
    if _is_auth_host(host):
        return False
    return _registrable(host) == _registrable(target_host)


async def _save_auth_state(context, url: str, flow_id: str) -> None:
    """Persist the signed-in session (cookies + localStorage) so the next run for
    this site is already authenticated — no manual sign-in needed again."""
    state_path = auth_state_path(url)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        await context.storage_state(path=str(state_path))
    except Exception as exc:
        log_error(flow_id, "auth: failed to save storage_state", exc)


async def _await_manual_login(context, page: Page, auth: AuthConfig, url: str,
                              flow_id: str, progress) -> None:
    """Open the sign-in page and let the user log in by hand.

    If a saved session was already loaded into this context, navigating to the
    login page redirects past it and we continue immediately. Otherwise we pause,
    the user signs in (signalling "done" with the Continue button, or we auto-
    detect that the sign-in form is gone), and the resulting session is saved so
    later runs skip sign-in entirely."""
    login_url = (auth.login_url or url).strip()
    target_host = (urlparse(url).hostname or "").lower()
    await _navigate(page, login_url)
    # Give an async login form a moment to render before deciding.
    await asyncio.sleep(1.5)
    if not await _login_field_visible(page) and _logged_in_on(page, target_host):
        await progress.publish(flow_id, "recording",
                               "Already signed in — continuing.", 15)
        return

    await progress.publish(
        flow_id, "awaiting_login",
        "Sign in manually in the browser window, then click “I’ve signed in” here.",
        12,
    )
    deadline = time.monotonic() + _MANUAL_LOGIN_TIMEOUT_S
    done_streak = 0  # consecutive checks that look signed-in — debounces transitions
    while time.monotonic() < deadline:
        if progress.is_cancelled(flow_id) or progress.is_stop_requested(flow_id):
            break
        if progress.is_continue_requested(flow_id):
            break
        if page.is_closed():
            break
        # Auto-continue ONLY once we're back on the product's own site with no
        # sign-in form. While the user is still on the identity provider (e.g.
        # typing the password on accounts.google.com), the host check keeps us
        # waiting — so we never navigate away mid-login.
        looks_done = (
            _logged_in_on(page, target_host) and not await _login_field_visible(page)
        )
        done_streak = done_streak + 1 if looks_done else 0
        if done_streak >= 3:
            break
        await asyncio.sleep(1.0)
    try:
        await page.screenshot(path=str(flow_dir(flow_id) / "auth_result.png"))
    except Exception as exc:
        log_error(flow_id, "auth: failed to save auth_result.png", exc)
    # Remember the session for next time (Playwright storage_state).
    if not page.is_closed():
        await _save_auth_state(context, url, flow_id)
    await progress.publish(flow_id, "recording", "Signed in — continuing.", 15)


async def _authenticate(context, page: Page, auth: AuthConfig, url: str,
                        flow_id: str, progress) -> None:
    """Run sign-in BEFORE any instruction. Manual sign-in only — the saved
    storage_state remembers it for next time."""
    if auth.method == AuthMethod.manual:
        await _await_manual_login(context, page, auth, url, flow_id, progress)


# ---------------------------------------------------------------------------
# Cross-loop progress proxy (see record_session docstring)
# ---------------------------------------------------------------------------


class _ThreadSafeProgress:
    """Proxies progress calls from a worker-thread event loop back to the main
    (uvicorn) loop, where the SSE asyncio.Queues live. asyncio.Queue.put_nowait
    is loop-bound and not thread-safe, so publish() is marshalled with
    run_coroutine_threadsafe; the cancellation reads are plain set lookups and
    safe to call from any thread."""

    def __init__(self, bus: ProgressBus, main_loop: asyncio.AbstractEventLoop) -> None:
        self._bus = bus
        self._main_loop = main_loop

    async def publish(self, *args, **kwargs) -> None:
        asyncio.run_coroutine_threadsafe(self._bus.publish(*args, **kwargs), self._main_loop)

    def is_cancelled(self, flow_id: str) -> bool:
        return self._bus.is_cancelled(flow_id)

    def is_stop_requested(self, flow_id: str) -> bool:
        return self._bus.is_stop_requested(flow_id)

    def is_continue_requested(self, flow_id: str) -> bool:
        return self._bus.is_continue_requested(flow_id)

    def raise_if_cancelled(self, flow_id: str) -> None:
        self._bus.raise_if_cancelled(flow_id)


async def _run_win_thread(make_coro):
    """Run a coroutine on a dedicated ProactorEventLoop in a worker thread."""
    def _run():
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
        try:
            return loop.run_until_complete(make_coro())
        finally:
            asyncio.set_event_loop(None)
            loop.close()

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def record_session(
    flow_id: str,
    url: str,
    actions: list[ParsedAction],
    auth: AuthConfig,
    progress: ProgressBus,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> tuple[Path, list[dict]]:
    """Execute all actions in a recorded browser session.

    On Windows, uvicorn forces a SelectorEventLoop when run with --reload (or
    --workers), and that loop cannot spawn subprocesses — so Playwright's
    attempt to launch the Chromium driver dies with NotImplementedError. We
    sidestep this by running the recording on a dedicated ProactorEventLoop in
    a worker thread (Proactor supports subprocesses), regardless of which loop
    uvicorn picked. Other platforms run inline on the current loop.
    """
    if sys.platform != "win32":
        return await _record_session_impl(
            flow_id, url, actions, auth, progress, viewport_width, viewport_height
        )
    safe_progress = _ThreadSafeProgress(progress, asyncio.get_running_loop())
    return await _run_win_thread(
        lambda: _record_session_impl(flow_id, url, actions, auth, safe_progress,
                                     viewport_width, viewport_height)
    )


async def record_manual_session(
    flow_id: str,
    url: str,
    auth: AuthConfig,
    progress: ProgressBus,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    max_minutes: float = 10.0,
) -> tuple[Path, list[dict]]:
    """Manual mode: open the browser, let the USER click through the product,
    and record everything. The user's clicks/typing/navigation are captured as
    timestamped actions (via an injected listener) so analysis produces the
    same step structure as automated runs. Recording ends when the user hits
    Stop in the UI, closes the browser window, or max_minutes elapses."""
    if sys.platform != "win32":
        return await _record_manual_impl(
            flow_id, url, auth, progress, viewport_width, viewport_height, max_minutes
        )
    safe_progress = _ThreadSafeProgress(progress, asyncio.get_running_loop())
    return await _run_win_thread(
        lambda: _record_manual_impl(flow_id, url, auth, safe_progress,
                                    viewport_width, viewport_height, max_minutes)
    )


async def _record_session_impl(
    flow_id: str,
    url: str,
    actions: list[ParsedAction],
    auth: AuthConfig,
    progress,
    viewport_width: int = 1280,
    viewport_height: int = 800,
) -> tuple[Path, list[dict]]:
    """Returns (mp4_path, action_timestamps) where each timestamp entry is
    {action_index, description, timestamp_seconds} captured at execution time."""
    rec_dir = recording_dir(flow_id)
    rec_dir.mkdir(parents=True, exist_ok=True)
    action_timestamps: list[dict] = []
    tracker = _TabTracker()
    tabs: list[tuple] = []
    recording_start = time.monotonic()
    sampler: asyncio.Task | None = None

    state = auth_state_path(url) if auth.method == AuthMethod.manual else None
    async with async_playwright() as p:
        browser, context = await _launch_context(
            p, viewport_width=viewport_width, viewport_height=viewport_height,
            rec_dir=rec_dir,
            headless=settings.headless,  # False by default → user can watch the automation
            slow_mo=settings.record_slow_mo,  # delay between actions (tunable in .env)
            storage_state=state,
        )
        try:
            await context.add_init_script(_STEALTH_SCRIPT)  # mask automation before page scripts run
            await context.add_init_script(_CURSOR_OVERLAY_SCRIPT)  # make the mouse visible in the video
            # A click may open a new tab (the site's target=_blank); track every
            # one so it can be stitched into the demo (see _TabTracker).
            context.on("page", tracker.register)
            page = await context.new_page()
            tracker.register(page)

            # 1. Sign in FIRST if needed (a saved session is reused automatically).
            await _authenticate(context, page, auth, url, flow_id, progress)

            # 2. Execute every parsed instruction automatically.
            recording_start = time.monotonic()
            sampler = asyncio.create_task(_run_sampler(tracker))
            total = len(actions)
            for i, action in enumerate(actions):
                if progress.is_cancelled(flow_id):
                    break
                if page.is_closed():
                    # User closed the browser window — stop gracefully and
                    # salvage whatever was recorded so far.
                    log_error(flow_id, "recorder: browser window was closed mid-run",
                              f"stopped before action {i}: {action.description}")
                    break
                entry = {
                    "action_index": i,
                    "description": action.description,
                    "timestamp_seconds": round(time.monotonic() - recording_start, 2),
                }
                action_timestamps.append(entry)
                print(f"Executing step {i + 1}/{total}: {action.description}")
                await progress.publish(
                    flow_id, "recording", f"Step {i + 1}/{total}: {action.description}",
                    15 + int(i / max(1, total) * 45),
                )
                try:
                    if action.type == "navigate" and action.url:
                        await _navigate(page, action.url)
                    elif action.type == "click":
                        coord = await _click(page, action, flow_id)
                        if coord:
                            entry["click_x"], entry["click_y"] = round(coord[0], 1), round(coord[1], 1)
                        await _settle(page)
                    elif action.type == "type":
                        await _type(page, action, flow_id)
                        await asyncio.sleep(0.4)
                    elif action.type == "press_key":
                        await page.keyboard.press(action.text or "Enter")
                        await _settle(page)
                    elif action.type == "wait":
                        await asyncio.sleep(action.duration or 1.0)
                    elif action.type == "scroll":
                        await page.mouse.wheel(0, 600)
                        await asyncio.sleep(0.5)
                    elif action.type == "hover" and action.selector:
                        await page.locator(action.selector).first.hover(timeout=5000)
                        await asyncio.sleep(0.5)
                    elif action.type == "screenshot":
                        await asyncio.sleep(0.5)  # the video itself is the capture
                except Exception as exc:
                    # One bad action never stops the pipeline — log it and move on.
                    log_error(flow_id, f"recorder: action {i} failed: {action.description}", exc)
                    continue

            await asyncio.sleep(1.5)  # capture the final state
            await tracker.sample()    # final reading so the last span is bounded
            tabs = tracker.video_tabs()
            # context.close() finalizes the .webm; do it before closing the
            # browser so the video file is flushed to disk.
        finally:
            if sampler is not None:
                sampler.cancel()
                try:
                    await sampler
                except Exception:
                    pass
            await _close_quietly(context, browser, flow_id)

    return await _finalize_recording(
        flow_id, rec_dir, tabs, tracker.timeline, recording_start,
        action_timestamps, viewport_width, viewport_height,
    )


async def _finalize_recording(
    flow_id: str, rec_dir: Path, tabs: list[tuple], timeline: list[tuple[float, int]],
    t0: float, action_timestamps: list[dict],
    viewport_width: int = 1280, viewport_height: int = 800,
    basename: str = "session",
) -> tuple[Path, list[dict]]:
    """Turn the recorded tab(s) into one MP4, then write the action timestamps.

    `tabs` is [(video_handle, created_monotonic)] for every tab that existed, in
    creation order; `timeline` is the [(monotonic_time, active_tab_index)] log of
    which tab was front-most over time; `t0` is the demo's zero point. When the
    demo only ever used one tab this collapses to the original single-video
    transcode. When a second tab opened (site- or user-spawned) the front tab's
    footage is cut and concatenated into a single continuous video.

    `basename` lets callers write somewhere other than the main session recording
    (e.g. an inserted-step mini-recording) without clobbering session.mp4."""
    await asyncio.sleep(2.0)  # give Playwright a moment to flush the file(s)
    mp4_path = rec_dir / f"{basename}.mp4"
    tab_webms = await _resolve_tab_webms(tabs)
    valid = sum(1 for path, _ in tab_webms if path is not None and path.exists())
    distinct_active = {idx for _, idx in timeline}

    stitched = False
    if valid >= 2 and len(distinct_active) >= 2:
        stitched = await _stitch_tabs(flow_id, tab_webms, timeline, t0, mp4_path,
                                      viewport_width, viewport_height)
    if not stitched:
        await _transcode_single(flow_id, rec_dir, tab_webms, mp4_path, basename)

    (rec_dir / f"{basename}_action_timestamps.json").write_text(
        json.dumps(action_timestamps, indent=2), encoding="utf-8"
    )
    return mp4_path, action_timestamps


async def _resolve_tab_webms(tabs: list[tuple]) -> list[tuple[Path | None, float]]:
    """Resolve each tab's recorded .webm on disk. Returns [(path|None, created)]
    aligned with `tabs` (so an index into it matches a tab index)."""
    out: list[tuple[Path | None, float]] = []
    for video, created_abs in tabs:
        path: Path | None = None
        if video is not None:
            try:
                path = Path(await video.path())
            except Exception:
                path = None
        out.append((path, created_abs))
    return out


async def _transcode_single(
    flow_id: str, rec_dir: Path, tab_webms: list[tuple[Path | None, float]],
    mp4_path: Path, basename: str,
) -> None:
    """Original single-video path: take the main tab's .webm (falling back to
    whatever .webm is on disk), give it a canonical name, transcode to MP4."""
    webm_path = tab_webms[0][0] if tab_webms else None
    if webm_path is None or not webm_path.exists():
        webm_path = _find_recording(rec_dir)
    if webm_path is None or not webm_path.exists():
        raise RuntimeError("Recording file not created — check FFmpeg and Playwright versions")

    # Canonical name so downstream code and debugging are predictable.
    session_webm = rec_dir / f"{basename}_recording.webm"
    if webm_path != session_webm:
        try:
            webm_path.replace(session_webm)
            webm_path = session_webm
        except Exception as exc:
            log_error(flow_id, "recorder: could not rename recording", exc)

    # Transcode to H.264 MP4: reliable duration metadata + frame-accurate seeking.
    await run_ffmpeg(
        ["-i", str(webm_path), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
         "-pix_fmt", "yuv420p", "-an", str(mp4_path)],
        timeout=600.0,
    )


def _build_segments(timeline: list[tuple[float, int]], t0: float) -> list[dict]:
    """Collapse the per-tick (monotonic_time, active_tab) log into contiguous
    runs [{idx, start, end}] in seconds relative to t0. Each run is one span the
    same tab stayed front-most; sub-frame runs are dropped."""
    runs: list[dict] = []
    for abs_t, idx in timeline:
        rel = abs_t - t0
        if runs and runs[-1]["idx"] == idx:
            runs[-1]["end"] = rel
        else:
            if runs:
                runs[-1]["end"] = rel  # the boundary closes the previous run
            runs.append({"idx": idx, "start": rel, "end": rel})
    return [r for r in runs if r["end"] - r["start"] >= 0.15]


async def _stitch_tabs(
    flow_id: str, tab_webms: list[tuple[Path | None, float]],
    timeline: list[tuple[float, int]], t0: float, mp4_path: Path,
    viewport_width: int, viewport_height: int,
) -> bool:
    """Assemble one MP4 that always shows the front-most tab by cutting each
    tab's .webm to the spans it was on screen and concatenating them in order.

    A tab's video time 0 is the moment the tab was created, so a demo-relative
    span [start, end] maps to that tab's footage at [start - created_T, end -
    created_T]. Returns True on success; False (caller falls back to the
    single-tab transcode) if the timeline can't be assembled or ffmpeg fails."""
    segments = _build_segments(timeline, t0)
    if len(segments) < 2:
        return False

    inputs: list[str] = []
    filters: list[str] = []
    n = 0
    for seg in segments:
        idx = seg["idx"]
        if idx >= len(tab_webms):
            continue
        webm, created_abs = tab_webms[idx]
        if webm is None or not webm.exists():
            continue
        created_t = created_abs - t0
        in_pt = max(0.0, seg["start"] - created_t)
        out_pt = seg["end"] - created_t
        if out_pt - in_pt < 0.15:
            continue
        inputs += ["-i", str(webm)]
        # Normalise fps/size/SAR so the concat filter accepts heterogeneous
        # inputs (different tabs can have slightly different cadence).
        filters.append(
            f"[{n}:v]trim=start={in_pt:.3f}:end={out_pt:.3f},setpts=PTS-STARTPTS,"
            f"fps=30,scale={viewport_width}:{viewport_height},setsar=1[v{n}]"
        )
        n += 1

    if n < 2:
        return False
    concat_in = "".join(f"[v{i}]" for i in range(n))
    filters.append(f"{concat_in}concat=n={n}:v=1:a=0[outv]")
    try:
        await run_ffmpeg(
            [*inputs, "-filter_complex", ";".join(filters), "-map", "[outv]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", "-an", str(mp4_path)],
            timeout=600.0,
        )
    except Exception as exc:
        log_error(flow_id, "recorder: multi-tab stitch failed; using single tab", exc)
        return False
    return mp4_path.exists()


def _find_recording(rec_dir: Path) -> Path | None:
    candidates = sorted(rec_dir.glob("*.webm"), key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0] if candidates else None


# ---------------------------------------------------------------------------
# Multi-tab tracking: follow every tab, log which one is front-most over time
# ---------------------------------------------------------------------------


class _TabTracker:
    """Watches every page (tab) in a recording context so a demo that opens new
    tabs — whether the site spawns one (target=_blank, popups) or the user does —
    can be reassembled into a single video that always shows the front-most tab.

    Playwright records each tab as its own independent .webm: a background tab
    keeps recording even while it isn't visible. We therefore keep every page's
    video handle plus the moment it was created, and poll document.visibilityState
    to log which tab is front-most over time. _finalize_recording uses both to cut
    each tab's footage to the spans it was actually on screen."""

    def __init__(self) -> None:
        self.pages: list[Page] = []
        self.created_abs: list[float] = []          # monotonic time each tab appeared
        self.timeline: list[tuple[float, int]] = []  # (monotonic_time, active_index)
        self._last_active = 0

    def register(self, page: Page) -> None:
        if page in self.pages:
            return
        self.pages.append(page)
        self.created_abs.append(time.monotonic())

    async def sample(self) -> None:
        """Log which tab is front-most right now. The visible tab reports
        visibilityState 'visible'; backgrounded tabs report 'hidden'. Iterating
        newest-first means a freshly opened tab (which comes to the front) wins
        immediately; if none reports visible (mid-transition) we hold the last."""
        active = self._last_active
        for i in range(len(self.pages) - 1, -1, -1):
            page = self.pages[i]
            if page.is_closed():
                continue
            try:
                if await page.evaluate("document.visibilityState") == "visible":
                    active = i
                    break
            except Exception:
                continue
        self._last_active = active
        self.timeline.append((time.monotonic(), active))

    def video_tabs(self) -> list[tuple]:
        """(video_handle, created_monotonic) for every tab, in creation order.
        Call before closing the context; video.path() is still resolvable after."""
        out: list[tuple] = []
        for page, created in zip(self.pages, self.created_abs):
            try:
                out.append((page.video, created))
            except Exception:
                out.append((None, created))
        return out


async def _run_sampler(tracker: _TabTracker, interval: float = 0.4) -> None:
    """Background task: keep sampling the front-most tab until cancelled. Used by
    automated mode, which has no human-driven wait loop of its own."""
    try:
        while True:
            await tracker.sample()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass


# ---------------------------------------------------------------------------
# Manual mode — the user drives, we watch
# ---------------------------------------------------------------------------

# Injected into every page: reports clicks / typing / Enter back to Python so
# the user's real interactions become timestamped step boundaries. Values are
# never captured (only field labels), so passwords stay out of the logs.
_CAPTURE_SCRIPT = """
(() => {
  if (window.__demoflowHooked) return;
  window.__demoflowHooked = true;
  const describe = (el) => {
    if (!el || !el.tagName) return "element";
    const text = (el.innerText || el.value || el.getAttribute("aria-label")
      || el.getAttribute("placeholder") || el.getAttribute("title")
      || el.getAttribute("alt") || "").trim().replace(/\\s+/g, " ").slice(0, 60);
    return text || el.tagName.toLowerCase();
  };
  // Listeners go on window, not document: the document object is replaced on
  // same-origin navigations while window (and our hooked-guard) persists, so
  // document-level listeners would silently die after the first navigation.
  // Clicks carry their real viewport coordinates so the generated demo can
  // anchor its highlight/ripple exactly where the user clicked.
  window.addEventListener("click", (e) => {
    const el = (e.target.closest && e.target.closest("a,button,[role=button],[role=tab],[role=menuitem],input,select,summary,label")) || e.target;
    try { window.__demoflow_log({ text: 'Click "' + describe(el) + '"', x: e.clientX, y: e.clientY }); } catch {}
  }, true);
  window.addEventListener("keydown", (e) => {
    if (e.key === "Enter") { try { window.__demoflow_log({ text: "Press Enter" }); } catch {} }
  }, true);
  window.addEventListener("change", (e) => {
    const el = e.target;
    if (!el || !el.tagName) return;
    const isPassword = (el.type || "").toLowerCase() === "password";
    const label = el.getAttribute("aria-label") || el.getAttribute("placeholder")
      || el.name || el.id || "a field";
    try { window.__demoflow_log({ text: isPassword ? "Enter password" : 'Type in "' + label + '"' }); } catch {}
  }, true);
})();
"""

# Playwright's video capture does NOT include the OS mouse cursor, so a manual
# (or automated) session would record with an invisible pointer. This injects a
# synthetic cursor INTO the page — a fixed SVG arrow that follows mousemove and
# flashes a ring on mousedown — so the real mouse is visible in the recording.
# Built via DOM APIs (no data-URI/escaping) and re-injected on every navigation.
_CURSOR_OVERLAY_SCRIPT = """
(() => {
  if (window.__demoflowCursor) return;
  window.__demoflowCursor = true;
  const SVGNS = 'http://www.w3.org/2000/svg';
  let cursor, ring;
  function build() {
    cursor = document.createElementNS(SVGNS, 'svg');
    cursor.id = '__demoflow_cursor';
    cursor.setAttribute('width', '24'); cursor.setAttribute('height', '32');
    cursor.setAttribute('viewBox', '0 0 24 32');
    cursor.style.cssText = 'position:fixed;left:0;top:0;z-index:2147483647;pointer-events:none;display:none;filter:drop-shadow(0 1px 2px rgba(0,0,0,.45));';
    const path = document.createElementNS(SVGNS, 'path');
    path.setAttribute('d', 'M3 2 L3 23 L9 17.5 L13 28 L16.5 26.6 L12.5 16.5 L20 16.5 Z');
    path.setAttribute('fill', '#ffffff');
    path.setAttribute('stroke', '#1b1b1b');
    path.setAttribute('stroke-width', '1.6');
    path.setAttribute('stroke-linejoin', 'round');
    cursor.appendChild(path);
    ring = document.createElement('div');
    ring.style.cssText = 'position:fixed;left:0;top:0;width:36px;height:36px;margin:-18px 0 0 -18px;border-radius:50%;border:3px solid rgba(234,179,8,.95);z-index:2147483646;pointer-events:none;opacity:0;';
    document.body.appendChild(cursor);
    document.body.appendChild(ring);
  }
  function ensure() { if (!document.body) return false; if (!cursor) build(); return true; }
  window.addEventListener('mousemove', (e) => {
    if (!ensure()) return;
    cursor.style.display = 'block';
    cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
  }, true);
  window.addEventListener('mousedown', (e) => {
    if (!ensure()) return;
    ring.style.left = e.clientX + 'px';
    ring.style.top = e.clientY + 'px';
    ring.animate(
      [{ opacity: 0.95, transform: 'scale(0.3)' }, { opacity: 0, transform: 'scale(1.5)' }],
      { duration: 450, easing: 'ease-out' }
    );
  }, true);
})();
"""

_MANUAL_MAX_ACTIONS = 500


async def _record_manual_impl(
    flow_id: str,
    url: str,
    auth: AuthConfig,
    progress,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    max_minutes: float = 10.0,
    rec_dir: Path | None = None,
    basename: str = "session",
) -> tuple[Path, list[dict]]:
    rec_dir = rec_dir or recording_dir(flow_id)
    rec_dir.mkdir(parents=True, exist_ok=True)
    action_timestamps: list[dict] = []
    tracker = _TabTracker()
    tabs: list[tuple] = []
    recording_start = time.monotonic()

    def _log_user_action(payload) -> None:
        """payload is either a plain string (navigation) or a dict {text, x, y}
        from the in-page capture script. Click coordinates, when present, are
        stored so the demo highlight lands exactly where the user clicked."""
        if len(action_timestamps) >= _MANUAL_MAX_ACTIONS:
            return
        entry = {
            "action_index": len(action_timestamps),
            "timestamp_seconds": round(time.monotonic() - recording_start, 2),
        }
        if isinstance(payload, dict):
            entry["description"] = str(payload.get("text") or "action")[:120]
            x, y = payload.get("x"), payload.get("y")
            if x is not None and y is not None:
                entry["click_x"], entry["click_y"] = round(float(x), 1), round(float(y), 1)
        else:
            entry["description"] = str(payload)[:120]
        action_timestamps.append(entry)

    state = auth_state_path(url) if auth.method == AuthMethod.manual else None
    async with async_playwright() as p:
        browser, context = await _launch_context(
            p, viewport_width=viewport_width, viewport_height=viewport_height,
            rec_dir=rec_dir,
            headless=False,  # manual mode is meaningless without a visible browser
            storage_state=state,
            no_viewport=True,  # real window → the user can physically scroll the page
        )
        try:
            await context.expose_binding(
                "__demoflow_log", lambda source, payload: _log_user_action(payload)
            )
            await context.add_init_script(_STEALTH_SCRIPT)  # mask automation before page scripts run
            await context.add_init_script(_CURSOR_OVERLAY_SCRIPT)  # make the mouse visible in the video
            await context.add_init_script(_CAPTURE_SCRIPT)

            def _watch_navigation(page: Page) -> None:
                page.on(
                    "framenavigated",
                    lambda frame: _log_user_action(f"Open {frame.url[:90]}")
                    if frame.parent_frame is None else None,
                )

            def _on_new_page(page: Page) -> None:
                # The user (or a site link) can open a new tab — track it so it's
                # stitched into the demo, and watch its navigation like the first.
                tracker.register(page)
                _watch_navigation(page)
            context.on("page", _on_new_page)

            page = await context.new_page()
            tracker.register(page)
            _watch_navigation(page)

            # Auth first (same as auto mode), then hand control to the user.
            await _authenticate(context, page, auth, url, flow_id, progress)
            recording_start = time.monotonic()  # reset: demo starts at the product page
            action_timestamps.clear()

            # The user can close the browser window during manual sign-in. Navigating
            # a closed page raises a Playwright TargetClosedError — surface a clear,
            # actionable message instead of a raw traceback. Genuine navigation errors
            # (timeouts, bad URLs) on a still-open page propagate unchanged.
            _browser_closed_msg = (
                "The browser window was closed before recording started, so nothing "
                "was captured. Start again and keep the window open until you press "
                "“Stop & Generate”."
            )
            if page.is_closed() or not context.pages:
                raise RuntimeError(_browser_closed_msg)
            try:
                await _navigate(page, url)
            except PlaywrightError:
                if page.is_closed() or not context.pages:
                    raise RuntimeError(_browser_closed_msg) from None
                raise

            await progress.publish(
                flow_id, "recording",
                "Recording — click through your product in the open browser. "
                "Press “Stop & Generate” here when you’re done.", 20,
            )

            deadline = time.monotonic() + max_minutes * 60
            while True:
                if progress.is_stop_requested(flow_id) or progress.is_cancelled(flow_id):
                    break
                if not context.pages or all(pg.is_closed() for pg in context.pages):
                    break  # user closed the browser — that also means "done"
                if time.monotonic() > deadline:
                    await progress.publish(
                        flow_id, "recording",
                        f"Reached the {max_minutes:.0f}-minute limit — finishing up…", 58,
                    )
                    break
                await tracker.sample()  # log which tab is front-most this tick
                await asyncio.sleep(0.5)

            open_pages = [pg for pg in context.pages if not pg.is_closed()]
            if open_pages:
                await asyncio.sleep(1.0)  # capture the final state
                await tracker.sample()   # final reading so the last span is bounded
            tabs = tracker.video_tabs()
        finally:
            await _close_quietly(context, browser, flow_id)

    return await _finalize_recording(
        flow_id, rec_dir, tabs, tracker.timeline, recording_start,
        action_timestamps, viewport_width, viewport_height, basename,
    )


async def record_insert_session(
    flow_id: str,
    url: str,
    auth: AuthConfig,
    progress: ProgressBus,
    insert_id: str,
    viewport_width: int = 1280,
    viewport_height: int = 800,
    max_minutes: float = 3.0,
) -> tuple[Path, list[dict]]:
    """Editor "Insert step here": open the browser, let the user perform the one
    forgotten action, and record it as a self-contained mini-clip. Identical to
    manual mode, but writes into recording/inserts/{insert_id}/ so it never
    overwrites the demo's main session recording."""
    rec_dir = recording_dir(flow_id) / "inserts" / insert_id
    if sys.platform != "win32":
        return await _record_manual_impl(
            flow_id, url, auth, progress, viewport_width, viewport_height,
            max_minutes, rec_dir=rec_dir, basename="insert",
        )
    safe_progress = _ThreadSafeProgress(progress, asyncio.get_running_loop())
    return await _run_win_thread(
        lambda: _record_manual_impl(flow_id, url, auth, safe_progress,
                                    viewport_width, viewport_height, max_minutes,
                                    rec_dir=rec_dir, basename="insert")
    )
