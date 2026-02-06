# JewelClaw OpenClaw Architecture Plan

## Current Working Features (DO NOT BREAK)
- ✅ Gold/Silver/Platinum rates via "gold" command
- ✅ Subscribe/Unsubscribe flow with name collection
- ✅ 9 AM Morning Brief to all subscribers
- ✅ Expert AI analysis (cached 1 hour)
- ✅ PostgreSQL database on Railway
- ✅ Rate scraping every 15 minutes

## Current Database Schema
```
users: id, phone_number, name, language, subscribed_to_morning_brief,
       preferred_city, created_at, updated_at, last_message_at, message_count

conversations: id, user_id, role, content, detected_language, created_at

metal_rates: id, city, rate_date, gold_24k, gold_22k, gold_18k, gold_14k,
             gold_10k, gold_9k, silver, platinum, gold_usd_oz, silver_usd_oz,
             platinum_usd_oz, usd_inr, mcx_gold_futures, mcx_silver_futures,
             source, recorded_at
```

---

## Phase 1: Conversation Intelligence (Week 1)
**Goal:** Store every conversation with intent detection

### Step 1.1: Add columns to existing conversations table
```sql
ALTER TABLE conversations ADD COLUMN intent VARCHAR(50);
ALTER TABLE conversations ADD COLUMN entities JSON DEFAULT '{}';
ALTER TABLE conversations ADD COLUMN sentiment VARCHAR(20);
```

### Step 1.2: Create memory_service.py (standalone, no changes to main.py yet)
- Intent detection patterns
- Entity extraction
- Sentiment analysis

### Step 1.3: Integrate memory_service into webhook (minimal change)
- Store user message with intent/entities
- Store assistant response
- NO changes to command handling logic

### Testing:
- Send "gold" → should work exactly as before
- Check database → conversations should have intent/entities

---

## Phase 2: Customer Database (Week 2)
**Goal:** Let jewelers store their customers

### Step 2.1: Create customers table
```sql
CREATE TABLE customers (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR(100) NOT NULL,
    phone VARCHAR(20),
    occasions JSON DEFAULT '[]',
    budget_min FLOAT,
    budget_max FLOAT,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Step 2.2: Create customer_service.py
- Add customer
- Search customer
- List customers

### Step 2.3: Add WhatsApp commands
- "add customer Rahul 9876543210" → Saves customer
- "customers" → Lists recent customers
- "find Rahul" → Searches customers

### Testing:
- All existing commands work
- New customer commands work

---

## Phase 3: Price Alerts (Week 3)
**Goal:** Alert users when gold hits target price

### Step 3.1: Create price_alerts table
```sql
CREATE TABLE price_alerts (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    metal VARCHAR(20) NOT NULL,
    condition VARCHAR(20) NOT NULL,
    target_price FLOAT NOT NULL,
    is_active BOOLEAN DEFAULT TRUE,
    triggered_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Step 3.2: Create alert_service.py
- Create alert
- Check alerts against current price
- Send alert notifications

### Step 3.3: Add to scheduler
- Check alerts every 15 minutes after rate scrape

### Step 3.4: Add WhatsApp commands
- "alert gold below 7500" → Creates alert
- "alerts" → Shows active alerts
- "cancel alert 1" → Removes alert

### Testing:
- Set alert for current price ± ₹10
- Wait for trigger

---

## Phase 4: Follow-up Suggestions (Week 4)
**Goal:** AI suggests when to contact customers

### Step 4.1: Create follow_ups table
```sql
CREATE TABLE follow_ups (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE,
    reason VARCHAR(100) NOT NULL,
    suggested_action TEXT,
    due_date DATE NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Step 4.2: Create followup_service.py
- Generate follow-ups based on:
  - Customer occasions (anniversary, birthday)
  - Last contact > 60 days
  - Price drops on interested items

### Step 4.3: Add to morning brief
- "You have 3 follow-ups today"

### Step 4.4: Add WhatsApp commands
- "followups" → Shows pending follow-ups
- "done 1" → Marks follow-up complete

---

## Phase 5: Design Trends (Week 5)
**Goal:** Track trending jewelry designs

### Step 5.1: Create designs table
```sql
CREATE TABLE designs (
    id SERIAL PRIMARY KEY,
    source VARCHAR(100) NOT NULL,
    image_url VARCHAR(500),
    title VARCHAR(200),
    category VARCHAR(50),
    style_tags JSON DEFAULT '[]',
    trending_score FLOAT DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW()
);
```

### Step 5.2: Create trend_service.py
- Placeholder scraping structure
- Trending score calculation

### Step 5.3: Add WhatsApp commands
- "trends" → Shows what's trending
- "trending necklaces" → Category filter

---

## Implementation Rules

1. **One phase at a time** - Complete and test before moving on
2. **Database migrations via endpoint** - Add /admin/migrate-phase-X endpoints
3. **Feature flags** - New features disabled by default until tested
4. **Backup commands** - Keep existing command logic, add new ones alongside
5. **Rollback plan** - Each phase can be disabled without breaking others

---

## Migration Strategy

Instead of DROP/CREATE, we use ALTER TABLE:

```python
@app.post("/admin/migrate-phase-1")
async def migrate_phase_1():
    """Add conversation intelligence columns."""
    async with engine.begin() as conn:
        await conn.execute(text("""
            ALTER TABLE conversations
            ADD COLUMN IF NOT EXISTS intent VARCHAR(50),
            ADD COLUMN IF NOT EXISTS entities JSON DEFAULT '{}',
            ADD COLUMN IF NOT EXISTS sentiment VARCHAR(20)
        """))
    return {"status": "Phase 1 migration complete"}
```

This way existing data is preserved and features keep working.

---

## Ready to Start?

Reply "start phase 1" to begin with Conversation Intelligence.
