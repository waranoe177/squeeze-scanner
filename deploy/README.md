# Sqzdots Bot — Fly.io Operator Runbook

## Deploy Instructions

### 1. Install flyctl

Download and install flyctl from https://fly.io/docs/getting-started/installing-flyctl/

### 2. Launch the app

```bash
fly launch --no-deploy
```

When prompted:
- Enter an app name (or accept the generated one)
- Decline adding a PostgreSQL database

### 3. Create the data volume

```bash
fly volumes create sqzdots_data --size 1 --region iad
```

### 4. Create a fine-grained GitHub Personal Access Token (PAT)

- Go to https://github.com/settings/tokens?type=beta
- Click "Generate new token"
- Name: `sqzdots-fly`
- Repository access: `waranoe177/squeeze-scanner` only
- Permissions → Repository permissions → Contents: `read and write`
- Expiration: ~1 year (set a calendar reminder to rotate before expiry)
- Click "Generate token" and copy it

### 5. Set Fly secrets

```bash
fly secrets set \
  TELEGRAM_BOT_TOKEN=<your-telegram-bot-token> \
  TELEGRAM_CHAT_ID=<your-telegram-chat-id> \
  GITHUB_TOKEN=<the-pat-from-step-4> \
  HEALTHCHECK_URL=<will-populate-in-step-6>
```

(For now, you can set `HEALTHCHECK_URL` to a placeholder; update it after step 6.)

### 6. Create a healthchecks.io check

- Go to https://healthchecks.io and log in (free account)
- Click "Add Check"
- Name: `sqzdots-bot`
- Period: ~15 minutes
- Grace: ~15 minutes
- Click "Save"
- Copy the ping URL (looks like `https://hc-ping.com/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`)
- Back in your terminal:

```bash
fly secrets set HEALTHCHECK_URL=<paste-the-ping-url>
```

### 7. Set a Fly spend cap

- Go to https://fly.io/dashboard → your organization → Billing → Spending Limit
- Set a cap (e.g., $10/month) to prevent runaway costs
- Save

### 8. Deploy the app

```bash
fly deploy
```

Watch the logs:

```bash
fly logs
```

### 9. Enforce single machine (no autoscaling)

```bash
fly scale count 1
```

### 10. Verify it's working

- Wait 2–5 seconds for the bot to connect to Telegram
- Send a test message to your Telegram chat: `trade COST`
- Expect a reply within 1–3 seconds with a chart
- Check the Fly logs for no errors:

```bash
fly logs
```

---

## Break-Glass: Manual Fallback Poller

If the Fly bot goes dark and healthchecks.io deadman alerts fire:

1. **Confirm Fly is down**
   - Run `fly status` or check https://fly.io/dashboard
   - Verify the machine is not running or has crashed

2. **Trigger the manual fallback poller** (only when Fly is confirmed down; do NOT trigger while Fly is running — two pollers briefly cause a 409 conflict)
   - Go to GitHub → **Actions** → **"Telegram Chart Bot"** (or similar cron workflow)
   - Click **"Run workflow"**
   - Choose `main` branch
   - Click **Run workflow**
   - This drains the queue until Fly is back online

3. **Monitor**
   - Watch the Actions workflow log to confirm it's running
   - Check your Telegram chat for chart messages
   - Once Fly is restored and confirmed healthy, you can cancel the workflow

---

## Rollback

If you need to disable the bot permanently or revert:

1. **Revert the bot.yml cron change** and re-enable the scheduled Actions workflow (if it was disabled):
   ```bash
   git revert <commit-that-disabled-cron>
   git push
   ```

2. **Scale down to 0** (stops the Fly machine):
   ```bash
   fly scale count 0
   ```

3. **Destroy the volume** (optional, if you want to remove all state):
   ```bash
   fly volumes delete sqzdots_data
   ```

4. **Destroy the app** (optional):
   ```bash
   fly apps destroy <app-name>
   ```

---

## Key Environment Variables

- `SQZDOTS_REPO`: Set to `waranoe177/squeeze-scanner` (in fly.toml)
- `SQZDOTS_STATE_PATH`: Set to `/data/telegram_state.json` (in fly.toml); persisted on the /data volume
- `TELEGRAM_BOT_TOKEN`: Telegram bot token (Fly secret)
- `TELEGRAM_CHAT_ID`: Telegram chat to send updates to (Fly secret)
- `GITHUB_TOKEN`: Fine-grained PAT for waranoe177/squeeze-scanner (Fly secret)
- `HEALTHCHECK_URL`: Ping URL from healthchecks.io for deadman monitoring (Fly secret)

---

## Sharing with friends & family (allowlist)

The bot is owner-only until you set an allowlist. To share:

1. Ask each person to message the bot once, then read the machine logs
   (`fly logs`) — an unlisted chat is logged as
   `update from unlisted chat <ID> ignored`. That `<ID>` is their Telegram
   chat id. (They can also DM `@userinfobot` on Telegram to get their id.)
2. Add the ids to the allowlist secret (comma-separated) and restart:

   ```bash
   fly secrets set TELEGRAM_ALLOWLIST=111,222,333
   ```

   Your own `TELEGRAM_CHAT_ID` is always allowed and does not need to be listed.
3. Remove someone by setting the secret again without their id.

Shared users can request `chart SYM` (any ticker) and `trade SYM` (tracked
universe only). They cannot log go/pass decisions (owner-only). They get a
one-time educational disclaimer and a 20-request/hour rate limit. The allowlist
lives ONLY in this Fly secret — never commit chat ids (the repo is public).

---

## Maintenance

- **GitHub PAT rotation**: Set a calendar reminder ~1 month before expiry (step 4); generate a new token and update the Fly secret
- **Healthchecks.io**: Check the dashboard monthly for any missed pings
- **Fly spend**: Review the Billing dashboard monthly to ensure costs stay under cap
