# JewelClaw - Railway Deployment Guide

## Prerequisites

- GitHub account
- Railway account (https://railway.app)
- Twilio account with WhatsApp Sandbox
- Anthropic API key (optional, for AI analysis)

---

## Step 1: Push Code to GitHub

```bash
# Initialize git (if not already done)
git init

# Add all files
git add .

# Commit
git commit -m "Initial JewelClaw deployment"

# Create repo on GitHub, then push
git remote add origin https://github.com/YOUR_USERNAME/jewelclaw.git
git branch -M main
git push -u origin main
```

---

## Step 2: Create Railway Project

1. Go to https://railway.app and sign in with GitHub
2. Click **"New Project"**
3. Select **"Deploy from GitHub repo"**
4. Choose your `jewelclaw` repository
5. Railway will auto-detect Python and start building

---

## Step 3: Add PostgreSQL Database

1. In your Railway project, click **"+ New"**
2. Select **"Database"** → **"Add PostgreSQL"**
3. Railway automatically creates a `DATABASE_URL` variable and links it to your app
4. The database URL is automatically shared with your app

---

## Step 4: Set Environment Variables

In Railway, go to your app service → **"Variables"** tab → **"Raw Editor"**

Paste these variables:

```
APP_NAME=JewelClaw
DEBUG=false
LOG_LEVEL=INFO
TIMEZONE=Asia/Kolkata
MORNING_BRIEF_HOUR=9
MORNING_BRIEF_MINUTE=0
MAX_MESSAGES_PER_HOUR=60
SCRAPE_INTERVAL_MINUTES=15
ANTHROPIC_API_KEY=sk-ant-api03-your-key-here
TWILIO_ACCOUNT_SID=your-twilio-account-sid
TWILIO_AUTH_TOKEN=your-twilio-auth-token
TWILIO_WHATSAPP_NUMBER=whatsapp:+14155238886
```

**Note:** Replace with your actual Twilio and Anthropic credentials.

---

## Step 5: Deploy

Railway automatically deploys when you:
- Push to GitHub
- Add environment variables
- Click "Deploy" button

Wait for the build to complete (2-3 minutes).

---

## Step 6: Get Your App URL

1. In Railway, go to your app service
2. Click **"Settings"** tab
3. Under **"Domains"**, click **"Generate Domain"**
4. You'll get a URL like: `https://jewelclaw-production.up.railway.app`

---

## Step 7: Configure Twilio Webhook

1. Go to https://console.twilio.com
2. Navigate to: **Messaging** → **Try it out** → **Send a WhatsApp message**
3. Click **"Sandbox settings"** (in the left sidebar)
4. Set these values:

**WHEN A MESSAGE COMES IN:**
```
https://YOUR-APP.up.railway.app/webhook/whatsapp
```

**Method:** POST

5. Click **"Save"**

---

## Step 8: Verify Deployment

### Health Check
Visit your app URL in browser:
```
https://YOUR-APP.up.railway.app/
```
Should return: `{"status":"healthy","app":"JewelClaw","version":"1.0.0"}`

### Scheduler Status
```
https://YOUR-APP.up.railway.app/scheduler/status
```
Should show morning_brief and rate_scraper jobs with next run times.

### Test WhatsApp
Send "gold" to your Twilio WhatsApp number. You should receive gold rates!

---

## Webhook URL Format

Your Twilio webhook URL will be:
```
https://YOUR-RAILWAY-APP-NAME.up.railway.app/webhook/whatsapp
```

Example:
```
https://jewelclaw-production.up.railway.app/webhook/whatsapp
```

---

## Monitoring & Logs

### View Logs in Railway
1. Go to your app service in Railway
2. Click **"Logs"** tab
3. Watch real-time logs

### Key Log Messages to Look For
```
Starting JewelClaw...
Using PostgreSQL database (production mode)
Database tables initialized
Scheduler started
Application started successfully
```

---

## Scheduled Jobs

| Job | Schedule | Description |
|-----|----------|-------------|
| Send Morning Briefs | 9:00 AM IST daily | Sends rates to subscribed users |
| Scrape Metal Rates | Every 15 min (9 AM - 9 PM IST) | Updates rate cache |

---

## Troubleshooting

### App won't start
- Check logs for errors
- Verify all environment variables are set
- Ensure DATABASE_URL is linked from PostgreSQL service

### Webhook not working
- Verify URL is correct with `/webhook/whatsapp` path
- Check Twilio sandbox is configured correctly
- Test with: `curl -X POST https://YOUR-APP.up.railway.app/webhook/whatsapp -d "From=whatsapp:+1234567890&Body=gold"`

### Database errors
- Railway PostgreSQL should auto-connect
- Check DATABASE_URL format: `postgresql://user:pass@host:port/db`

### Morning brief not sending
- Verify timezone is `Asia/Kolkata`
- Check scheduler status endpoint
- Ensure users are subscribed (send "subscribe" via WhatsApp)

---

## WhatsApp Commands

| Command | Description |
|---------|-------------|
| `gold` | All gold karat rates (24K, 22K, 18K, 14K) |
| `silver` | Silver rates (per gram and per kg) |
| `platinum` | Platinum rates |
| `analysis` | Full market analysis |
| `subscribe` | Subscribe to 9 AM morning brief |
| `unsubscribe` | Unsubscribe from daily updates |
| `help` | Show all commands |

---

## Cost Estimate (Railway)

- **Hobby Plan:** $5/month (includes 500 hours)
- **PostgreSQL:** Included in plan
- **Expected usage:** ~720 hours/month for 24/7 operation
- **Recommended:** Developer plan or add usage-based billing

---

## Support

- Railway Docs: https://docs.railway.app
- Twilio WhatsApp: https://www.twilio.com/docs/whatsapp
- JewelClaw Issues: Create GitHub issue
