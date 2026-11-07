# Design: signing in to fetch remote data, and saying where data comes from

Status: **design, 2026-11-07; reviewed (§9), decisions taken; nothing built.** Branch
`feat/remote-sign-in`. Issue ID prefix: **RSI**. Follows SES-A (`ses-hdf-support.md` §2, remote
hdf files, 0.0.23). Its first user is the SES cloud (BigO `claude.design.limelight-remote.md`),
but nothing here is specific to it.

**Guiding rule (2026-11-07): keep it tight, security-wise.** Wherever there was a choice, the
tighter option is taken.

## 1. The problem

A package can name its HDF5 by URL (SES-A), but Limelight fetches it with an unauthenticated
GET. Private data then needs a secret in the URL, which turns the package into a credential.
Instead, **the app signs in**, and the package holds plain URLs. A forwarded package is harmless:
its recipient gets "you don't have access" unless they do.

Two more rules:

- Every trip off the machine is obvious to the reader.
- The reader is asked whether to remember their decision.

## 2. The standards

| Step | Standard | What it gives |
|---|---|---|
| The server says a token is needed, and where to get one | RFC 6750 §3, RFC 9728 §5.1 | `401` + `WWW-Authenticate: Bearer resource_metadata="…"` |
| What the protected resource is, and who issues its tokens | RFC 9728 | `{resource, authorization_servers, scopes_supported}` |
| The issuer's endpoints | RFC 8414 | `issuer`, `device_authorization_endpoint`, `token_endpoint`, `revocation_endpoint` |
| Signing in from a desktop app | RFC 8628 | Device authorization grant: a code, the browser, polling. No local port and no redirect. |
| A token for one resource only | RFC 8707 | `resource=` on every authorization and token request |
| Signing out | RFC 7009 | revocation |

**Only the device flow is supported (RSI-D1).** Authorization Code + PKCE is not built.

**Client id.** Limelight is the public client `limelight`. A server that wants Limelight must
accept that id. Dynamic registration and Client ID Metadata Documents are not built (RV-12).

**One deliberate deviation from RFC 9728 (RV-2).** §3.3 wants `resource` *identical* to the URL
that was refused, which would mean one sign-in per file. Limelight, like MCP clients, accepts a
`resource` that is a **prefix of the refused URL on path-segment boundaries**. For example,
`https://h/api/v1` covers `https://h/api/v1/files/…`, but `/data` does not cover `/data-evil`.
The token is stored under that `resource`.

## 3. The flow

```
open package → show the window (empty figures say "waiting for data")      (RV-1)
  └─ remote files not in the cache?
       └─ host Blocked → placeholders, nothing contacted
       └─ host not Allowed → §4.1 dialog ── Don't download → placeholders
                                         └ Download
       └─ the window bar: "Loading data from <host>…"                       (§4.3)
            └─ worker thread: GET url, with this resource's token if one is held
                 ├─ 200 → check size + SHA-256 → cache by digest → redraw
                 ├─ 401 from the ORIGINAL origin → NeedsSignIn(challenge) to the GUI thread (RV-4)
                 │     └─ discovery (§5 rules 2-4) → §4.2 sign-in → retry this file once
                 ├─ 403 insufficient_scope → sign in again for that scope
                 ├─ 403 / 404 → "Your account <who> cannot read this data on <host>"
                 ├─ 409 → "This data has changed on <host> since the package was made: get a new package"
                 └─ 429 / 503 + Retry-After → wait (max 120 s each), at most 3 tries, shown in the bar
```

A cached file needs no network and no token: the cache is keyed by digest, as today.

## 4. What the reader sees

### 4.1 Before anything leaves the machine

This dialog is shown **before any request**, metadata included, when a package names
remote files that are not cached and their host is neither Allowed nor Blocked. It is modal,
parented to the window, and has one row per host:

```
┌───────────────────────────────────────────────────────────────┐
│  This package loads its data from the internet                │
│                                                               │
│    dev0.super-easy-sensors.com                                │
│    3 files · 182.4 MB          ▸ Show files and URLs          │
│    You may be asked to sign in to this site.                  │
│                                                               │
│  Nothing has been downloaded yet.                             │
│                                                               │
│  ☐ Remember my decision for dev0.super-easy-sensors.com       │
│                                                               │
│                         [ Don't download ]  [ Download ]      │
└───────────────────────────────────────────────────────────────┘
```

- **The host as shown:**
  - It is the URL's **exact host**, in large type, with the registrable domain
    (`super-easy-sensors.com`) in bold. The registrable domain comes from the Public Suffix List
    (`publicsuffixlist`, offline).
  - IDN hosts are shown as punycode (`xn--…`), with the Unicode form after it in brackets (RV-9).
- **Remember my decision** applies to the **exact host only**, never its subdomains (RSI-Q1):
  - Download + Remember → **Allowed**.
  - Don't download + Remember → **Blocked**.
  - Without Remember the answer lasts until the package is closed.
- **Default button:** none. Enter does nothing; the reader must click.

### 4.2 Signing in

Shown on the GUI thread when a host answers 401 and discovery passes §5:

```
┌───────────────────────────────────────────────────────────────┐
│  Sign in to dev0.super-easy-sensors.com                       │
│                                                               │
│  Limelight will open your browser at                          │
│    https://dev0.super-easy-sensors.com/show/devices/approve   │
│  Sign in there and type this code:                            │
│                                                               │
│                    BKDF-QRTW                                  │
│                                                               │
│  Only type it on that site. Limelight will never show a code  │
│  you did not ask for.                                         │
│                                                               │
│  ☐ Remember this sign-in on this computer                     │
│                                                               │
│  Waiting for you to approve…    [ Copy link ]  [ Cancel ]     │
└───────────────────────────────────────────────────────────────┘
```

- **The browser link:**
  - The browser opens at `verification_uri`, **never `verification_uri_complete`**. The reader
    types the code; it is not pre-filled.
  - The URI must be https, on the authorization server's registrable domain; otherwise the
    sign-in is refused.
- **Polling:** at `interval`, default 5 s. `slow_down` adds 5 s for this poll and every later
  one. Polling stops at `expires_in`. The dialog shows the outcome: approved (it closes),
  "Denied", or "Expired: start again".
- **Remember this sign-in** means the token is kept in the OS keychain for this `resource`.
  Unchecked, it is held in memory until Limelight quits.
- **No keychain:** headless Linux, or a keyring error. There is **no file fallback**: the token
  is memory-only, and the dialog says so (RV-8, tight).

### 4.3 The trip is always visible

The window has a bar whenever the open package uses remote data, Allowed hosts included:

- "Loading data from **dev0.super-easy-sensors.com**… 2 of 3 files [Cancel]" while it fetches;
- then "Data from **dev0.super-easy-sensors.com** · signed in as x@y [Sign out]", for as long as
  the package is open.

The window is shown **before** fetching on every path: first open (today it fetches in
`LimelightWindow.__init__`, `qt_app.py:2956`), Open and Reload (RV-1).

### 4.4 Settings › Data from the internet

- Each remembered host is listed with its state (Allowed / Blocked) and a Forget button.
- Each sign-in is listed as "<resource> · signed in as <who>", with a Sign out button. Sign out
  revokes the token (RFC 7009, if offered) and forgets it.
- **Delete downloaded data** empties the download cache.
- Decisions are read **only from the user's config folder**. `limelight-setting.json` in the
  current directory can no longer allow a host (RV-7).
- **Migration:** each `trustedDownloadHosts` entry in `app-settings.json` becomes an **Allowed
  exact host**. Its subdomains are no longer covered, and this is listed in the CHANGELOG.
  Entries in a hand-written `limelight-setting.json` are not migrated.
- **Precedence:** Blocked wins over Allowed.

## 5. Security rules

1. **https only**, for data, metadata, endpoints and the verification URI. A redirect to http is
   refused (RV-6).
2. **The resource metadata:**
   - It is fetched only from the URL in `resource_metadata`, which must be on the **same origin**
     as the refused request.
   - A 401 without `resource_metadata` falls back to the RFC 9728 §3 well-known URI on that
     origin; if that does not answer, the sign-in fails with a message.
   - Its `resource` must cover the refused URL (§2, the deviation).
3. **The authorization server must be on the same registrable domain as the resource.** If not,
   the sign-in is **refused outright**, with "This site sends you to sign in at <other domain>;
   Limelight does not allow that". There is no override. This closes the confused-deputy hole:
   a malicious host naming the reader's real university sign-in to obtain its token (RV-3).
4. **Issuer metadata:**
   - Its URL is derived as RFC 8414 §3.1 says, with the well-known segment inserted between host
     and path. OIDC `openid-configuration` is appended to the issuer.
   - `issuer` must equal the issuer named.
   - It must offer the device grant.
5. **A token goes only to its resource:**
   - It is stored by `resource`, sent with `add_unredirected_header`, and re-attached by a custom
     redirect handler **only on a same-origin redirect**.
   - A cross-origin redirect is followed without it (a store's presigned URL works).
   - A 401 from a redirect target never starts a sign-in.
6. **Never written anywhere but the keychain:** tokens stay out of packages, logs, exception
   texts, the cache, settings and PDFs. In logs a token is shown as `Bearer …<last 4>`.
7. **No request before consent** (§4.1), and every trip is visible (§4.3).
8. **The cache** is the user's, readable by whoever can read their cache folder. Signing out does
   not delete it; *Delete downloaded data* does.

## 6. Command line

- **`LL login <url>`** runs discovery from the URL given (the RFC 9728 §3 well-known URI), then
  the device flow in the terminal: it prints the link and the code, then waits. The token goes to
  the keychain. Without a keychain it **fails**: there is nowhere safe to keep it.
- **`LL logout <resource>`** revokes and forgets the token.
- **`LL verify` / `LL pdf`** use keychain tokens.
  - `--allow-downloads` consents to hosts that are not Allowed. **Blocked hosts are refused even
    with it**; this is a change from today (`cli.py:47-56`).
  - A 401 with no token says `run: LL login <url>`.
  - Progress and every URL go to stderr.
- **CI:** `LIMELIGHT_BEARER_TOKEN` and `LIMELIGHT_BEARER_RESOURCE` give exactly one token for
  exactly one `resource`. They are never written anywhere.

## 7. Code

| Change | Where |
|---|---|
| Challenge parsing, discovery and its checks (§5 rules 2-4), the device flow, the token store | new `src/limelight/remote_auth.py` (no Qt) |
| Typed outcomes from `download()` (`NeedsSignIn`, `NoAccess`, `Changed`, `RetryAfter`): the status is read before an `HTTPError` becomes `RemoteFileError` (`remote_files.py:179`). Token provider; redirect handler; https only | `remote_files.py` |
| Host decisions (Allowed / Blocked, exact), config-folder only, migration | `settings.py` |
| Dialogs §4.1 and §4.2, the bar §4.3, Settings §4.4. The window is shown before fetching | `qt_downloads.py`, `qt_app.py` |
| `LL login` / `LL logout`; tokens and Blocked hosts in `verify` / `pdf` | `cli.py` |
| `keyring` and `publicsuffixlist` become core dependencies. PyInstaller: `copy_metadata("keyring")`, the backends as hidden imports, and a packaged-app smoke test | `pyproject.toml`, `packaging/limelight.spec` |

The tests run against a local https test server with its own CA. The metadata and token fetches
take the same injectable `opener` / SSL context as `download()`.

| Area | Cases |
|---|---|
| Good path | the whole chain, end to end |
| Discovery | inconsistent `resource`; a cross-domain authorization server (refused); a wrong `issuer` |
| Redirects | https → http (refused); same-origin (token kept); cross-origin (token dropped); a 401 from a redirect target |
| Device flow | `slow_down`, denied, expired, `insufficient_scope` |
| Errors | 403, 409, 429, 503 |
| Settings | the migration; a settings file in the working folder (ignored) |
| Dialogs | Blocked beats Allowed; the dialog decisions |
| Tokens | never in logs or exceptions |

**No manifest change.** Whether a URL needs a sign-in is discovered from the 401, so packages
already written keep working.

**Also in this release (for BigO, LLR WR):** a writer mode with no local file,
`add_hdf_dataset(remote=RemoteFile(url, sha256, size_bytes), shapes={dataset: (dtype, shape)}, …)`.
Bindings are checked against the declared shapes; `LL verify` checks them against the real file
once it is downloaded.

## 8. Decisions (2026-11-07)

| ID | Question | Decision |
|----|----------|----------|
| RSI-Q1 | Does a remembered Allow cover subdomains? | **No.** Exact hosts only, including migrated entries. |
| RSI-Q2 | Dynamic client registration? | No. `client_id=limelight`. |
| RSI-Q3 | Authorization Code + PKCE? | No. Device flow only. |
| RSI-Q4 | Which scope to ask for? | The resource metadata's `scopes_supported` if given, else none. The server decides what it grants. |
| RSI-D2 | An authorization server on another domain | Refused, with no override (§5 rule 3). |
| RSI-D3 | No keychain | The token is memory-only in the app; `LL login` fails. No file fallback. |

## 9. Independent review, 2026-11-07

An independent review checked this note against the code and the RFCs. **Every resolution
below is folded into §1-§8.** Where they differ (RV-3, RV-7, RV-8), §1-§8 win: they take the
tighter option.

| ID | Sev. | Finding | Proposed resolution |
|----|------|---------|---------------------|
| RV-1 | Blocker | The first open fetches inside `LimelightWindow.__init__` (`qt_app.py:2956`, `fetch_remote_files(None, …)`) before the window exists. The progress dialog has no parent and `setMinimumDuration(300)` (`qt_downloads.py:155`), so a fast download from an Allowed host shows nothing. This breaks §4.1. | Show the window first, then fetch, then redraw, on every path (first open, Open, Reload). The bar lives in the window. |
| RV-2 | Blocker | §5 rule 2 ("`resource` is a prefix") contradicts RFC 9728 §3.3 / §7.3: when the metadata came from `resource_metadata`, `resource` MUST be identical to the refused URL. | **A deliberate deviation, as MCP's:** `resource` must be a prefix of the refused URL **on path-segment boundaries** (`/api/v1` matches `/api/v1/files/…`; `/data` does not match `/data-evil`). The token is stored under that `resource` value. Say so in §5 and §2. |
| RV-3 | Important | Confused deputy: a malicious host can list the reader's *real* authorization server (e.g. their university's). If that server ignores RFC 8707 `resource`, the reader signs in genuinely and the token goes to the evil host (RFC 9728 §7.4 / §7.6). | If the authorization server's registrable domain differs from the resource's, refuse by default. Allow it only after an explicit, strongly worded approval for that (resource, authorization server) pair, stored with the host decision. Never reuse a token across resources. |
| RV-4 | Important | `download()` runs in a worker thread (`qt_downloads.py:168-191`), so a 401 arrives off the GUI thread. All `HTTPError`s become `RemoteFileError` (`remote_files.py:179`), losing the status. | The worker returns a typed `NeedsSignIn(challenge)` (and `NoAccess`, `Changed`, `RetryAfter`), with the status and headers read before wrapping. The GUI thread runs discovery and the device flow, then restarts that file. |
| RV-5 | Important | Protocol details were missing. | Send `resource` on device authorization, on every poll and on refresh. `slow_down`: add 5 s for this and all later polls. Use 5 s if `interval` is absent; stop at `expires_in`. Keep refresh tokens: `invalid_token` → refresh → drop → device flow. 403 `insufficient_scope` → sign in again with the scope asked for. A 401 without `resource_metadata` → try the RFC 9728 §3 well-known URI on the same origin, else fail with a clear message. `verification_uri(_complete)` must be https; show its host. RFC 8414: for an issuer with a path, insert the well-known segment between host and path; OIDC appends `/.well-known/openid-configuration`. `issuer` must be equal. |
| RV-6 | Important | Redirects. `add_unredirected_header` also drops the token on a *same-origin* redirect, and urllib follows https → http. | A custom redirect handler: re-attach the token only to the same origin; refuse any redirect to http. Start a sign-in only for a 401 from the original origin, never from a redirect target. |
| RV-7 | Important | Host decisions also come from `limelight-setting.json`, which is looked up first in the **current directory** (`settings.py:24,63`), so a file where the reader runs can grant consent. Also, today's "Always allow" stores an exact host but `host_is_trusted` (`remote_files.py:118-125`) gives it its subdomains. | Read host decisions only from the user's config folder. Migrate from `app-settings.json` only, and keep those entries as "covers subdomains", so behaviour doesn't change. New dialog decisions are exact-host. **Blocked beats Allowed** when both match. |
| RV-8 | Important | `keyring`: Windows Credential Manager caps a secret at 2560 bytes. Linux needs Secret Service (fails or blocks headless). PyInstaller needs `copy_metadata("keyring")` plus hidden imports (`packaging/limelight.spec`). macOS asks again after a re-signed update. In the `gui` extra, the CLI has no keyring, so `LL login` is pointless. | Make `keyring` a core dependency. Store only the refresh token (or the opaque token when there is no refresh) and keep access tokens in memory. If no keyring works, fall back to a 0600 file in the config folder, with a one-time note. Add the PyInstaller metadata and hidden imports, plus a packaged-app smoke test. |
| RV-9 | Important | Look-alike hosts: bolding "the registrable part" needs the Public Suffix List, and IDN hosts are accepted today (`remote_files.py:92`). | Show IDN hosts as punycode (`xn--…`) with the Unicode form in brackets. Use the `publicsuffixlist` package (small, offline) for the registrable domain in RV-3 and the bolding. |
| RV-10 | Minor | CLI gaps: `LL login <host>` has no 401 to start from. `LIMELIGHT_BEARER_TOKEN_<HOST>` can't hold `-` and collides. The meaning of `--allow-downloads` changes (blocked hosts). There is no progress output. | `LL login <url>` derives the RFC 9728 well-known URI from the URL given. Env var: `LIMELIGHT_BEARER_TOKEN` + `LIMELIGHT_BEARER_RESOURCE` (one pair, an exact `resource`). Document that blocked hosts are refused even with `--allow-downloads`. Progress goes to stderr. |
| RV-11 | Minor | 409 `superseded` is a BigO convention, not a standard. | Keep it as a nicer message when seen. The size and SHA-256 check is the real guard. |
| RV-12 | Minor | `client_id=limelight` must be registered by hand on every server. | RSI-Q2: consider Client ID Metadata Documents (an https-URL `client_id`, MCP's current preference) later. For now a fixed id is enough. BigO accepts it explicitly. |
| RV-13 | Minor | Tests. | The metadata and token fetches take the same injectable `opener` / SSL context as `download()`. Add cases: a cross-domain authorization server; https → http; a same-origin redirect; a 401 from a redirect target; refresh; `insufficient_scope`; a settings file in the working folder. |

Confirmed by the review:
- In the GUI, every download goes through `fetch_remote_files` on the GUI thread; the large-series, table and CSV paths never fetch (`app.py:814`).
- The current dialog comes before any request.
- The CLI fetches through `_download_policy` only.
- The cache is keyed by digest, so tokens cannot reach it.
- `requires-python >=3.10`, where `add_unredirected_header` behaves as assumed.
