# JewelClaw - AI WhatsApp Assistant for Indian Jewelry Industry

An intelligent WhatsApp bot powered by Claude AI, designed specifically for jewelry manufacturers and retailers in India.

## Features

### Gold Intelligence
- Live gold/silver rates in ₹/gram and ₹/10gram
- Rates for 24K, 22K, 18K, 14K gold
- IBJA official rates (benchmark for India)
- International XAU/USD with rupee impact
- Direction analysis and market sentiment
- Actionable buying/selling insights

### Daily Morning Brief
Proactive WhatsApp message at 8am IST with:
- Current gold and silver rates
- Daily change indicators
- Weekly trend analysis
- Market insights

### Multilingual Support
Responds in English, Hindi, or Hinglish based on user's language preference.

## Tech Stack

- **Python 3.11+**
- **FastAPI** - Webhook handling
- **Claude API** - AI conversations
- **PostgreSQL** - Data persistence
- **SQLAlchemy** - ORM
- **BeautifulSoup4** - Web scraping
- **APScheduler** - Scheduled jobs
- **Twilio** - WhatsApp integration

## Setup

### 1. Clone and Install

```bash
git clone <repository-url>
cd jewelry-agent
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Environment Variables

```bash
cp .env.example .env
# Edit .env with your credentials
```

### 3. Database Setup

```bash
# Create PostgreSQL database
createdb jewelclaw

# Tables are created automatically on first run
```

### 4. Run Locally

```bash
uvicorn app.main:app --reload --port 8000
```

### 5. Expose for Twilio (Development)

```bash
ngrok http 8000
# Use the HTTPS URL for Twilio webhook
```

## Twilio Setup

1. Go to [Twilio Console](https://console.twilio.com)
2. Navigate to Messaging > Try it out > Send a WhatsApp message
3. Join the sandbox by sending the code to the Twilio number
4. Set webhook URL: `https://your-domain.com/webhook/whatsapp`

## Railway Deployment

```bash
# Install Railway CLI
npm i -g @railway/cli

# Login and deploy
railway login
railway init
railway up
```

Set environment variables in Railway dashboard.

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Health check |
| `/webhook/whatsapp` | POST | Twilio webhook |
| `/rates/gold` | GET | Current gold rates |
| `/rates/gold/history` | GET | Historical rates |

## Project Structure

```
jewelry-agent/
├── app/
│   ├── main.py              # FastAPI application
│   ├── config.py            # Settings management
│   ├── database.py          # Database connection
│   ├── models.py            # SQLAlchemy models
│   ├── services/
│   │   ├── claude_service.py    # Claude AI integration
│   │   ├── gold_service.py      # Gold rate scraping
│   │   ├── whatsapp_service.py  # Twilio handling
│   │   └── scheduler_service.py # Cron jobs
│   └── utils/
│       └── language_detector.py # Language detection
├── requirements.txt
├── .env.example
└── README.md
```

## License

MIT
# Force redeploy Sat, Feb  7, 2026  5:15:00 PM - OpenClaw v2
