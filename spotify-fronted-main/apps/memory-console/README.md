# memory-console

The main app: log in, then the seven screens - each showing only your own
data. See the repository README one level up for what each screen is for, and
`docs/` for a document per screen.

```bash
npm install
npm run dev        # http://localhost:3000
npm run build      # production build
```

The backend must be running on the address in `.env.local.example`, and that
file also needs the backend's `MEMORY_JWT_SECRET`, used to check the pass a
person gets when they log in.

Log in or sign up at http://localhost:3000/login.
