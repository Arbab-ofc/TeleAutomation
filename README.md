# Tele Automation

Tele Automation is a local-first Telegram automation workspace. It uses your own Telegram user account through Telethon/MTProto, allowing you to select multiple writable groups or channels, compose one message, schedule delivery, and monitor the run from a responsive dashboard.

The project is designed for authorized accounts and communities. It does not bypass Telegram permissions, FloodWait, slow mode, account restrictions, or privacy controls.

## What is included

- React + Vite responsive homepage and workspace
- Telegram API ID/API hash setup with OTP and 2FA flow
- Multi-group and multi-channel destination selection
- Saved message templates and send-once testing
- Smart scheduling: start time, weekdays, time windows, timezone offset, and message limits
- Start, pause, resume, and stop controls
- FloodWait/SlowMode handling and transient send retries
- Live activity log, delivery history, job state, health and metrics endpoints
- 12-digit time-bound license keys with date-range or duration expiry
- Admin portal with HttpOnly session cookie, audit log, rate limiting, and key revoke/delete
- Firebase Realtime Database support with local fallback for development
- API documentation, Swagger UI, ReDoc, and a downloadable Postman collection
- Pricing page with Telegram prefilled buyer-contact messages
- Branded 404 page with automatic five-second redirect

## Stack

- **Frontend:** React, TypeScript, Vite, Tailwind CSS, Lucide icons
- **Backend:** Python 3.11+, FastAPI, Uvicorn, Telethon, Pydantic
- **Persistence:** Firebase Realtime Database in production; local JSON fallback in development
- **Telegram session:** Local Telethon session under `backend/sessions/`

## Requirements

- Python 3.11 or newer
- Node.js 18 or newer and npm
- A Telegram account
- Telegram API credentials from [my.telegram.org/apps](https://my.telegram.org/apps)
- Optional: Firebase Realtime Database and Firebase service account for production persistence

## Installation

### Backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### Frontend

```bash
cd frontend
npm install
```

## Environment configuration

Create `backend/.env` from the example:

```bash
cp backend/.env.example backend/.env
```

Minimum recommended values:

```env
APP_ENV=development
COOKIE_SECURE=false
ADMIN_ACCESS_CODE=replace-with-a-long-private-admin-code
LICENSE_TOKEN_SECRET=replace-with-a-long-random-secret
TELEGRAM_API_ID=12345678
TELEGRAM_API_HASH=your-telegram-api-hash
ADMIN_TELEGRAM_USERNAME=your_telegram_username
AUTOMATION_RESUME_ON_START=false
```

For Firebase production persistence:

```env
FIREBASE_DATABASE_URL=https://your-project-default-rtdb.firebaseio.com
FIREBASE_SERVICE_ACCOUNT_JSON={"type":"service_account","project_id":"..."}
```

Never commit the Firebase service-account JSON, `.env`, Telegram session, or local data files. They are ignored by `.gitignore`. Prefer deployment-platform secrets or a cloud secret manager in production.

In development, the frontend defaults to `http://localhost:8000/api`. Production builds default to same-origin `/api`, which is intended to be reverse-proxied to FastAPI. To override either environment:

```bash
cp frontend/.env.example frontend/.env
```

Then set:

```env
VITE_API_URL=https://your-api.example.com/api
```

## Running locally

Run backend and frontend in separate terminals:

```bash
cd backend
source venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

```bash
cd frontend
npm run dev
```

Open [http://localhost:5173](http://localhost:5173).

Or use the combined launcher from the project root:

```bash
chmod +x start.sh
./start.sh
```

If port 8000 is already occupied:

```bash
lsof -nP -iTCP:8000 -sTCP:LISTEN
kill <PID>
```

## First Telegram connection

1. Open [my.telegram.org](https://my.telegram.org) and choose **API development tools**.
2. Create an application and copy the numeric API ID and API hash.
3. In Tele Automation, open **Settings** and save those credentials.
4. Open **Telegram Account**, enter your phone number in international format, for example `+919876543210`.
5. Enter the Telegram verification code and, if requested, your Telegram cloud password.
6. The local Telethon session will reconnect on later backend starts while it remains valid.

OTP and Telegram 2FA values are not persisted by the application.

## Automation workflow

1. Open the workspace and select one or more writable destinations.
2. Compose a message or load a saved template.
3. Configure interval and optional smart scheduling rules.
4. Enter a valid 12-digit license key.
5. Use **Send once** for a test or **Start automation** for recurring delivery.

The minimum interval is 10 seconds. Messages are sent sequentially to selected destinations. FloodWait and slow mode delays are respected; temporary network failures use bounded exponential retries.

Automation recovery is disabled by default. When `AUTOMATION_RESUME_ON_START=true`, only jobs persisted as both desired `RUNNING` and observed `RUNNING`/`WAITING` are eligible to resume. Recovery revalidates Telegram authorization, the internal license record, and every destination. It schedules the next future eligible delivery and skips any delivery left in an uncertain in-flight state rather than risking a duplicate. Stopped, paused, completed, errored, and rate-limited jobs never auto-resume.

Delivery-history retention is also opt-in. Set `DELIVERY_RETENTION_ENABLED=true` to apply the conservative defaults of 90 days and 10,000 records, configurable through `DELIVERY_RETENTION_DAYS`, `DELIVERY_RETENTION_MAX_RECORDS`, and `DELIVERY_RETENTION_INTERVAL_SECONDS`.

## Admin portal and licenses

Open the homepage and choose the admin portal, or visit `/#admin`.

1. Enter the `ADMIN_ACCESS_CODE` from `backend/.env`.
2. Generate a key by duration or exact date range.
3. Copy the key immediately—the raw key is shown only once.
4. Revoke/delete keys from the license list when needed.
5. Set the buyer Telegram username in the **Buyer contact** field. Pricing buttons open that account with a prefilled message; the user manually presses Telegram’s Send button.

License records are stored under Firebase `license_keys` when Firebase is enabled. Expired and revoked/deleted records are removed from the active database. Admin actions are retained in `backend/data/audit.log` locally or can be forwarded to your deployment logging system.

## API documentation

With the backend running:

- Swagger UI: [http://localhost:8000/api/docs](http://localhost:8000/api/docs)
- ReDoc: [http://localhost:8000/api/redoc](http://localhost:8000/api/redoc)
- OpenAPI JSON: [http://localhost:8000/api/openapi.json](http://localhost:8000/api/openapi.json)
- Postman collection: [http://localhost:8000/api/docs/postman.json](http://localhost:8000/api/docs/postman.json)

Useful endpoints:

| Endpoint                           | Purpose                                                       |
| ---------------------------------- | ------------------------------------------------------------- |
| `GET /api/health`                | Telegram state, automation state, persistence backend, uptime |
| `GET /api/ready`                 | Readiness check                                               |
| `GET /api/metrics`               | Runtime counters                                              |
| `GET /api/telegram/dialogs`      | Writable destination discovery                                |
| `POST /api/automation/start`     | Start a scheduled automation                                  |
| `GET /api/automation/status`     | Current run state                                             |
| `GET /api/automation/deliveries` | Persistent delivery history                                   |
| `POST /api/licenses/validate`    | Validate a user license key                                   |
| `GET /api/licenses/admin/audit`  | Protected admin audit log                                     |

Example license validation:

```bash
curl -X POST http://localhost:8000/api/licenses/validate \
  -H 'Content-Type: application/json' \
  -d '{"key":"123456789012"}'
```

## Firebase data layout

```text
license_keys/{record_id}
runtime/job
runtime/deliveries/{delivery_id}
app_config/admin_contact
```

When Firebase is not configured, runtime data falls back to:

```text
backend/data/automation_job.json
backend/data/delivery_history.jsonl
backend/data/contact.json
```

## Security checklist

- Keep `.env`, Firebase service-account JSON, and `backend/sessions/` private.
- Use a long random `LICENSE_TOKEN_SECRET` and `ADMIN_ACCESS_CODE`.
- Deploy behind HTTPS and set secure cookies in production.
- Restrict Firebase Realtime Database rules to the service account/server.
- Rotate Firebase credentials immediately if a secret is ever committed.
- Do not expose the admin portal publicly without an additional network or identity-control layer.
- Keep Telegram API credentials and session access limited to the machine running the backend.

## Troubleshooting

**Admin list returns 500 or looks like a CORS error**

Check the backend terminal traceback first, then restart Uvicorn after code or `.env` changes. The application allows both `localhost:5173` and `127.0.0.1:5173` by default.

**No destinations appear**

Confirm Telegram authorization, press refresh, and ensure the account can post in the selected group/channel.

**License is invalid immediately after generation**

Confirm Firebase credentials and database URL, restart the backend, and generate a new key. The backend verifies the Firebase write before returning the key.

**Address already in use**

Find and stop the existing process with `lsof -nP -iTCP:8000 -sTCP:LISTEN`.

## Validation

```bash
cd backend
source venv/bin/activate
python -m compileall app
```

```bash
cd frontend
npm run build
```

## Project structure

```text
backend/
  app/
    models/       Pydantic schemas and runtime state
    routes/       Telegram, automation, licenses, settings, messages
    services/     Telegram lifecycle, licenses, scheduler, persistence
    utils/        event logging and rate limiting
  data/           Local fallback data and logs
  sessions/       Telethon session files
frontend/
  public/         Favicon and static assets
  src/
    pages/        API documentation page
    services/     Central API client
    App.tsx       Homepage, workspace, admin and guide views
start.sh          Combined local development launcher
```

Developed by Abby.
