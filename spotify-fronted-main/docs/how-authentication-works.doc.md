# How authentication works — logging in, and seeing only your own data

**Files:** `app/login/page.tsx`, `app/api/auth/[action]/route.ts`,
`lib/session.ts`, `app/api/backend/[...path]/route.ts`,
`components/SubjectBar.tsx`, `lib/useSubject.ts`. Backend:
`memory/accounts.py`, `memory/auth.py`.

---

## The short version

Open the console, **sign up** with a user id nobody else has and a password
(or **log in**), and use it. Every page then shows only your own data.
Locally, the five test users can log in too, with the password set as
`DEMO_PASSWORD` in the backend's private `.env`; on a server they have no
login.

---

## Why there is a login

`abc.md` assumes Spotify's own login: the API gateway checks the listener's
existing Spotify session and hands out a token. This pilot has no Spotify
session to check, so it stands in for that step with a user id and password.
The README states it as a limitation.

Two kinds of caller, two ways in:

| Caller | How it gets a token |
|---|---|
| A person, in this app | logs in - `POST /auth/login` checks the password and returns a 15-minute pass |
| A service (an AI surface, Postman) | a token minted with the shared secret - `scripts/make_token.py` |

---

## The flow

```
browser                    console server                       backend
   |                            |                                  |
   |-- POST /api/auth/login --->|                                  |
   |   user id + password       |-- POST :8000/auth/login -------->|
   |                            |                                  |-- check scrypt hash
   |                            |<-- { token, expires_in } --------|
   |                            |-- put token in httpOnly cookie   |
   |<-- { subject_id } ---------|   (the page never sees it)       |
   |                            |                                  |
   |-- POST /api/backend/ ----->|                                  |
   |   v1/context/compose       |-- check the pass (lib/session)   |
   |   (no credentials)         |-- write YOUR id into the request |
   |                            |-- POST :8000/v1/context/... ---->|
   |                            |   Authorization: Bearer <pass>   |-- check the pass again
   |                            |                                  |-- bind_subject: pass id
   |<---------------------------|<---------------------------------|   must match the data
```

---

## How "only your own data" is enforced

1. **Authentication, twice.** The console checks the pass's signature and
   expiry (`lib/session.ts`), and the backend checks it again on every call
   (`memory/auth.py`). No pass or an expired one means *"Please log in"* and
   the login page.
2. **The user id comes from the pass, never the browser.** The gateway writes
   it into every request's query string and body, overwriting anything the
   page sent. A page cannot ask for somebody else even by mistake.
3. **The backend refuses a mismatch.** A pass for `user_001` asking for
   `user_002`'s data gets `403 SUBJECT_MISMATCH`.
4. **Only the paths the pages need are allowed.** `GET /subjects`, which lists
   every user, is refused (`NOT_ALLOWED_HERE`).

Overview and Quality review show **counts only** - no user ids, no memory text
- which is why every logged-in user can see them.

---

## How passwords and logins are kept safe (`memory/accounts.py`)

- Stored only as a **salted scrypt hash**, never as text.
- A **taken user id is refused**, including the five test users, so nobody can
  sign up as somebody else.
- **5 wrong passwords** pause logins for that id for 15 minutes.
- A wrong id and a wrong password get **the same answer**, so nobody can find
  out which ids exist.
- The pass sits in an **httpOnly cookie** - page scripts cannot read it.
- Sign-ups and logins are **audited**, never with the password.

---

## Functions

| Function | File | Why it exists |
|---|---|---|
| `LoginPage` | `app/login/page.tsx` | The log in / sign up form |
| `POST` / `GET` | `app/api/auth/[action]/route.ts` | signup, login, logout, me - keeps the pass in the cookie, returns only the user id |
| `readSession()`, `saveSession()`, `clearSession()` | `lib/session.ts` | Read, check, store and forget the pass |
| `proxy()` | `app/api/backend/[...path]/route.ts` | Checks the pass, allows only known paths, writes the user id in, forwards |
| `SubjectBar` | `components/SubjectBar.tsx` | "Logged in as", memory on / paused / off, log out; sends a logged-out visitor to the login page |
| `useSubject()` | `lib/useSubject.ts` | The logged-in user's id, for pages that show it |
| `create_account()`, `check_login()` | backend `memory/accounts.py` | The account store and password check |

---

## Worth pointing at in a demo

- **Log in as `user_005`** (consent paused): the context package comes back
  `no_memory: true` with *"consent is paused"* - not an error (`abc.md:158`).
- **Log in as `user_003`** (memory-disabled experiment arm): always answered
  without memory - the baseline for the quality comparison.
- **Pause memory** from the top bar, compose again, and see the same.
- **Log out** and try any page: straight back to the login screen.
