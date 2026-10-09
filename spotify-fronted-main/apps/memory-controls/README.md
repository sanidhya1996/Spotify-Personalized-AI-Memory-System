# memory-controls

The listener page: review, correct, remove, pause or switch off what the AI
remembers about you. `abc.md:253` - *"Review, correction, deletion UI"*.

See `docs/memory-controls.doc.md` in the repository root for the call flow, and
the repository README for how this fits beside the main app.

```bash
npm install
cp .env.local.example .env.local     # add the backend MEMORY_JWT_SECRET
npm run dev                          # http://localhost:3001
npm run build
```

## How it works

**Log in first.** Each listener signs up or logs in with their own user id and
password (`app/login/page.tsx`). The gateway
takes the user id from the login pass and writes it into every request, so
nothing the browser sends can reach anybody else's memories.

**The gateway is not a general proxy.** It allows only search, one memory, one
deletion job, feedback, consent and health. Anything else answers
`403 NOT_ALLOWED_HERE`.

**Pause and opt out work.** Both change consent through `PATCH /v1/consent`,
take effect on the very next request, and delete nothing.
