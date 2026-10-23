# Authentication

Auth runs **once, before any instruction**, in both auto and manual modes. It's configured
in the UI's "Authentication" panel and sent as the `auth` object on `POST /api/record`.

**Code:** `_authenticate()` in `services/browser_recorder.py`. **Model:** `AuthConfig` in
`models/flow.py`.

## The four methods

| Method | UI fields | What the backend does |
|---|---|---|
| **`none`** | — | No-op. Use when the product is public or you'll sign in by hand in the visible browser. |
| **`credentials`** | login URL, username, password | Navigates to the login URL, fills the email/username + password fields, clicks submit, waits for the URL to change (one retry). Works for **standard HTML login forms**. |
| **`cookies`** | cookie string | Parses a `name=value; name2=value2` string and injects the cookies for the target domain **before navigation** — the site sees you as already logged in. |
| **`token`** | bearer token | Sets `Authorization: Bearer <token>` on all requests, and also adds an `auth_token` cookie. |

A **persistent browser profile** (`output/sessions/{flow_id}`) backs every run, so a manual
login done during one recording is remembered if you re-run that same flow.

## Recommended: the cookie method

For sites behind Google/SSO sign-in (which block scripted login), **cookies are the stable
path** — you log in once in your *own* browser and hand the session to the recorder, so no
automated sign-in happens at all.

### How to get the cookie string

Use the **Network tab** (gives the exact format the field wants):

1. Log in to the site normally in Chrome.
2. **F12** → **Network** tab → refresh the page (**F5**).
3. Click the first request (usually the page URL itself).
4. Under **Request Headers**, find the line starting with **`cookie:`**.
5. Copy everything after `cookie:` and paste it into the cookie field. Set method = *cookies*.

Alternative (Application tab): **F12 → Application → Cookies →** your domain, then build
`name1=value1; name2=value2` from the table manually.

### Gotchas

- **Copy the cookies *after* you've signed in**, not from the login page.
- **`HttpOnly` cookies don't appear in the Console's `document.cookie`** — that's why the
  Network-tab method is recommended; it shows them.
- **Cookies expire.** If a later run fails auth, re-copy a fresh string.
- **`__Host-` / `__Secure-` prefixed cookies** have strict rules (a `__Host-` cookie must
  not carry a domain). The parser sets a domain on every cookie, so such prefixed cookies
  can be rejected by the browser. If a site relies on them, prefer the token method or a
  manual sign-in.

## When cookies won't help

Some apps don't authenticate the *API* with cookies at all. For example, an app may identify
the user purely by a query param (e.g. `?user_id=you@example.com`) and keep the login token
on the **frontend in localStorage or IndexedDB** (typical of **Firebase / Google Auth**).
In that case:

- There is no session cookie to copy.
- The cleanest route is **manual mode**: open the visible browser, sign in by hand, then
  click through. The persistent profile remembers the login for re-runs of that flow.

## Security notes

- Credentials and tokens are sent to the local backend and used only to drive the browser;
  they are **not** written into `flow.json` or `action_timestamps.json`.
- The manual-mode capture script records field **labels**, never field **values**, so typed
  passwords never reach the logs.
- An `auth_result.png` screenshot is saved after a credentials login for debugging.
