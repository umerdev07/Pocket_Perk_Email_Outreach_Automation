# PocketPerks Outreach

PocketPerks is a web workspace for importing local-business leads, preparing and reviewing campaign emails, and managing scheduled sending and replies. App data, workspace accounts and background jobs are stored in PostgreSQL. AI generation and mailbox sending remain external integrations.

## Docker deployment

The Compose stack runs PostgreSQL, a migration job, the backend with its workers, and the frontend. PostgreSQL is private to the Compose network. Frontend and backend ports bind to `127.0.0.1` so the existing Nginx service can proxy traffic without exposing these ports publicly.

1. Copy `.env.example` to `.env`. Set a unique URL-safe PostgreSQL password and the actual app origin. For local use, the origin can be `http://localhost:3000`; for deployment, use the confirmed HTTPS app hostname.
2. Copy `backend/.env.example` to `backend/.env`. Set a unique `AUTH_JWT_SECRET`, SMTP settings, AI and sending-provider credentials. For a public HTTPS deployment, set `AUTH_COOKIE_SECURE=true` and keep `SEND_MODE=test` until live sending is explicitly reviewed.
3. Start the services from this directory:

   ```powershell
   docker compose up --build -d
   ```

   Compose waits for PostgreSQL, applies pending numbered migrations, then starts the backend and frontend. The database volume persists across container rebuilds.
4. Create the first administrator once:

   ```powershell
   docker compose exec backend python -m app.bootstrap_admin
   ```

   Follow the prompts to set the admin email and password. Do not place the password in shell history or Git.
5. Check `http://localhost:3000` locally, or verify the site through Nginx after its virtual host and TLS certificate are configured. The browser uses a same-origin `/api` route; no service-domain URL is embedded in frontend configuration.

The initial PostgreSQL database is fresh. Existing data from an earlier hosted database is not copied automatically. Keep that old project and its backups intact until the new deployment has been accepted.

## Operations and migration notes

- See [POSTGRES_MIGRATION.md](POSTGRES_MIGRATION.md) for the PostgreSQL migration inventory, setup notes, and deployment constraints.
- Keep `.env`, `backend/.env`, and all credentials out of Git.
- Back up the PostgreSQL volume before applying migrations or updating the application.
- Do not expose PostgreSQL port 5432 publicly. Do not stop or alter other applications, databases, Redis, or Nginx services already on the VPS.
- For more than one backend worker/replica, review sender capacity and ensure only the intended worker services are running.
