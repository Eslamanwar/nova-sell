# NovaSell — Autonomous AI Dubizzle Sales Agent

> Fully autonomous selling agent powered by **AWS Nova AI** that operates on **Dubizzle UAE**

NovaSell automates the entire lifecycle of selling items on Dubizzle: creating listings, interacting with buyers, negotiating prices, answering calls, and scheduling pickups — all powered by Amazon Nova AI models.

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                    NovaSell Agent System                         │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │  Listing      │  │ Conversation │  │  Call Agent          │  │
│  │  Agent        │  │ Agent        │  │  (Nova Sonic)        │  │
│  │  (Nova Pro)   │  │ (Nova Pro)   │  │  Speech-to-Speech    │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────┴───────┐  ┌──────┴───────┐  ┌──────────┴───────────┐  │
│  │ Negotiation  │  │  Scheduling  │  │  CAPTCHA HITL        │  │
│  │ Agent        │  │  Agent       │  │  Service             │  │
│  │ (Nova Pro)   │  │ (Nova Pro)   │  │  (Human-in-Loop)     │  │
│  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘  │
│         │                 │                      │              │
│  ┌──────┴─────────────────┴──────────────────────┴───────────┐  │
│  │              Workflow Orchestrator (Temporal)              │  │
│  └──────┬────────────────────────────────────────────────────┘  │
│         │                                                       │
│  ┌──────┴────────────────────────────────────────────────────┐  │
│  │              Browser Automation (Nova Act)                │  │
│  │              + Anti-Ban Strategy                          │  │
│  └──────┬────────────────────────────────────────────────────┘  │
│         │                                                       │
│  ┌──────┴────────┐  ┌──────────────┐  ┌──────────────────────┐  │
│  │ Memory Store  │  │ Notification │  │  Config Manager      │  │
│  │ (Redis/PG)    │  │ Service      │  │  (pydantic-settings) │  │
│  └───────────────┘  └──────────────┘  └──────────────────────┘  │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────┴─────────┐
                    │   Dubizzle UAE    │
                    │   dubai.dubizzle  │
                    └───────────────────┘
```

## AI Model Stack

| Model | Role | Responsibilities |
|-------|------|-----------------|
| **Nova Lite** | Vision/Multimodal | Object detection, image analysis, brand/model identification |
| **Nova Pro** | Reasoning | Listing generation, pricing, conversation, negotiation, scheduling |
| **Nova Sonic** | Speech-to-Speech | Real-time phone conversations with buyers |
| **Nova Act** | Browser Automation | Navigate Dubizzle, fill forms, upload images, publish listings, read messages |

## System Capabilities

### 1 — Listing Creation Agent
Automatically posts items on Dubizzle with AI-generated content.

```
Upload Photo → Nova Lite detects item → Nova Pro prices it
→ Nova Pro generates listing → User approves → Nova Act posts to Dubizzle
```

**Features:**
- Optimized title generation (SEO for Dubizzle)
- Compelling description with Dubai market focus
- Smart pricing based on comparable Dubizzle listings
- Category auto-selection
- Image upload automation

### 2 — CAPTCHA Handling (Human-in-the-Loop)
Dubizzle CAPTCHA is solved via HITL with browser streaming.

```
Nova Act detects CAPTCHA → Workflow pauses → Notification sent
→ Human solves CAPTCHA in browser view → Workflow resumes
```

### 3 — Buyer Conversation Agent
Autonomously responds to buyer inquiries on Dubizzle chat.

**Example:**
```
Buyer: "Is the item still available?"
Agent: "Yes, it is available! The item is in excellent condition
        and located in Dubai. Would you like to arrange a viewing?"

Buyer: "Can you do 900 AED?"
Agent: (reasoning: min_price=950, counter=980)
       "I appreciate your offer! The lowest I can go is 980 AED.
        This is a great deal considering the condition."
```

### 4 — Phone Call Handling (Nova Sonic)
Real-time voice conversations with buyers.

```
Incoming call → Nova Sonic receives audio → Understands intent
→ Generates spoken response → Replies in real time
```

**Capabilities:** Answer questions, negotiate, schedule pickup, escalate to human

### 5 — Negotiation Agent
Strategic price negotiation with configurable boundaries.

**Strategy:**
- Start from listed price
- Counter at 5-8% below listed on first offer
- Smaller concessions each round (2-3%)
- Never go below minimum acceptable price
- Escalate to human for edge cases

### 6 — Human-in-the-Loop (HITL)
Human intervention for:
- ✅ CAPTCHA solving (browser streaming)
- ✅ High-value negotiations (approval workflow)
- ✅ Suspicious buyers (trust scoring)
- ✅ Call escalation
- ✅ Payment confirmation
- ✅ Workflow pause/resume

### 7 — Anti-Ban Strategy
Prevents Dubizzle bot detection:
- 🕐 Random delays between actions (1-3s)
- ⌨️ Human-like typing speed with variable delays
- 🔄 Session/cookie reuse (persistent browser profile)
- 📊 Rate limiting (max listings/hour, messages/minute)
- 🖱️ Simulated mouse movements
- 🌐 Browser fingerprint management

## Project Structure

```
project/
├── __init__.py
├── acp.py                          # ACP server configuration
├── config.py                       # Centralized configuration (pydantic-settings)
├── constants.py                    # AI prompts and model constants
├── activities.py                   # Temporal activities (agent entry points)
├── workflow.py                     # Main Temporal workflow orchestrator
├── run_worker.py                   # Temporal worker runner
│
├── models/                         # Domain models (Pydantic)
│   ├── __init__.py
│   ├── listing.py                  # ObjectAnalysis, PriceEstimate, ListingContent, PostingResult
│   └── conversation.py            # ChatMessage, NegotiationContext, VoiceSession, HITLRequest
│
├── services/                       # Service layer
│   ├── __init__.py
│   ├── nova_llm.py                # Nova Pro/Lite LLM service (OpenAI-compatible)
│   ├── nova_sonic.py              # Nova Sonic voice service (speech-to-speech)
│   ├── browser_automation.py      # Dubizzle Nova Act automation + HITL
│   ├── anti_ban.py                # Anti-ban strategy (delays, rate limiting, fingerprint)
│   ├── memory_store.py            # State management (listings, conversations, negotiations)
│   └── notification_service.py    # Slack/email notifications for HITL
│
├── state_machines/                 # State machine definitions
│   ├── __init__.py
│   └── novasell_agent.py          # NovaSellState, NovaSellData, NovaSellStateMachine
│
└── workflows/                      # Workflow state implementations
    ├── __init__.py
    ├── terminal_states.py          # Sold, Completed, Failed, Cancelled
    └── sell/
        ├── __init__.py
        ├── waiting_for_image.py    # Wait for photo upload
        ├── object_detection.py     # Nova Lite image analysis
        ├── pricing.py              # Nova Pro market pricing
        ├── listing_generation.py   # Nova Pro listing copywriting
        ├── awaiting_approval.py    # User review and approval
        ├── publishing.py           # Nova Act Dubizzle posting
        └── active_listing.py       # Chat, negotiation, voice, scheduling
```

## State Machine Flow

```
WAITING_FOR_IMAGE
    │
    ▼
OBJECT_DETECTION (Nova Lite)
    │
    ▼
PRICING (Nova Pro)
    │
    ▼
LISTING_GENERATION (Nova Pro)
    │
    ▼
AWAITING_APPROVAL
    │
    ├── approve → PUBLISHING (Nova Act → Dubizzle)
    ├── edit    → LISTING_GENERATION
    ├── price   → AWAITING_APPROVAL
    └── cancel  → CANCELLED
    │
    ▼
ACTIVE_LISTING
    │
    ├── buyer chat    → Conversation Agent (Nova Pro)
    ├── negotiation   → Negotiation Agent (Nova Pro)
    ├── phone call    → Call Agent (Nova Sonic)
    ├── scheduling    → Scheduling Agent (Nova Pro)
    ├── "sold"        → SOLD ✅
    ├── "remove"      → COMPLETED
    └── "status"      → show stats
```

## Getting Started

```bash
# Install dependencies
uv sync

# Set environment variables
cp .env.example .env
# Edit .env with your credentials

# Run the agent
uvicorn project.acp:acp --host 0.0.0.0 --port 8000
```

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `OPENAI_API_KEY` | LLM gateway API key (OpenRouter/LiteLLM) | Yes |
| `OPENAI_BASE_URL` | LLM gateway URL | Yes |
| `AWS_REGION` | AWS region for Nova services | Yes |
| `AWS_ACCESS_KEY_ID` | AWS access key | Yes |
| `AWS_SECRET_ACCESS_KEY` | AWS secret key | Yes |
| `DUBIZZLE_EMAIL` | Dubizzle account email | Yes |
| `DUBIZZLE_PASS` | Dubizzle account password | Yes |
| `TEMPORAL_ADDRESS` | Temporal server address | Yes |
| `NOVA_ACT_USER_DATA_DIR` | Browser profile directory | No |
| `IMAGE_STORAGE_DIR` | Image storage path | No |
| `SLACK_WEBHOOK_URL` | Slack notifications webhook | No |
| `REDIS_URL` | Redis URL for caching | No |
| `DATABASE_URL` | PostgreSQL URL for persistence | No |
| `ALLOWED_EMAILS` | Comma-separated allowed user emails | No |
| `MIN_ACTION_DELAY` | Min delay between browser actions (seconds) | No |
| `MAX_ACTION_DELAY` | Max delay between browser actions (seconds) | No |
| `MAX_LISTINGS_PER_HOUR` | Rate limit: listings per hour | No |
| `MAX_LISTINGS_PER_DAY` | Rate limit: listings per day | No |

## Agent Activities

| Activity | Agent | Model | Description |
|----------|-------|-------|-------------|
| `detect_object` | Object Detection | Nova Lite | Analyze image for item identification |
| `estimate_price` | Pricing | Nova Pro | Market value estimation (AED) |
| `generate_listing` | Listing Generation | Nova Pro | Dubizzle listing copywriting |
| `handle_chat_message` | Conversation | Nova Pro | Buyer chat responses |
| `negotiate_price` | Negotiation | Nova Pro | Strategic price negotiation |
| `handle_voice_session` | Call Handler | Nova Sonic | Real-time voice conversations |
| `handle_scheduling` | Scheduling | Nova Pro | Pickup/viewing coordination |
| `post_listing_to_marketplace` | Browser Automation | Nova Act | Dubizzle form filling & posting |
| `respond_to_marketplace_chat` | Browser Automation | Nova Act | Dubizzle chat response automation |
| `upload_image_to_disk` | Storage | — | Image persistence |

## Technology Stack

| Layer | Technology |
|-------|-----------|
| **Language** | Python 3.12 |
| **AI Models** | Amazon Nova (Lite, Pro, Sonic, Act) |
| **Orchestration** | Temporal.io (state machine workflows) |
| **API** | FastAPI (via AgentEx FastACP) |
| **Browser** | Nova Act SDK (Playwright-based) |
| **LLM Gateway** | OpenAI-compatible (LiteLLM/OpenRouter) |
| **State** | In-memory (Redis/Postgres ready) |
| **Notifications** | Slack webhooks, SMTP email |
| **Config** | pydantic-settings (env vars) |
