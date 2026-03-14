# NovaSell — Autonomous AI Marketplace Sales Agent

## Try it now on https://hub.rilo.dev/
## See already running chat with Nova sell https://hub.rilo.dev/ui?agent_name=novasell&task_id=9bf89f61-af93-4c98-9fbe-30e83bcb4041

> **Fully autonomous selling agent** powered by **AWS Amazon Nova AI** that operates on **Shozon UAE**, **Dubizzle**, and **Facebook Marketplace** — handling every step from photo to sold.

NovaSell automates the complete lifecycle of selling items on online marketplaces: analyzing product photos, generating listings, posting ads (with CAPTCHA HITL), responding to buyers, negotiating prices in real time, handling phone calls via voice AI, and scheduling pickups — all orchestrated by Temporal state machines.

---

## Table of Contents

1. [System Architecture](#system-architecture)
2. [AI Model Stack](#ai-model-stack)
3. [State Machine Flow](#state-machine-flow)
4. [Workflow Deep-Dives](#workflow-deep-dives)
5. [Nova Sonic Voice Calls](#nova-sonic-voice-calls)
6. [Browser Automation & HITL](#browser-automation--hitl)
7. [Anti-Ban Strategy](#anti-ban-strategy)
8. [Data Models](#data-models)
9. [Services](#services)
10. [Activities Reference](#activities-reference)
11. [Configuration Reference](#configuration-reference)
12. [Project Structure](#project-structure)
13. [Deployment](#deployment)
14. [Getting Started](#getting-started)

---

## System Architecture

### High-Level System

```mermaid
graph TB
    subgraph Client["Client / Chat UI"]
        U["User (Seller)"]
        CHAT["AgentEx Chat"]
    end

    subgraph Agent["NovaSell Agent — Kubernetes Pod"]
        ACP["ACP HTTP Server\n(FastAPI :8000)"]
        WF["NovaSellWorkflow\n(Temporal State Machine)"]

        subgraph Activities["Temporal Activities"]
            A1["detect_object\n(Nova Lite)"]
            A2["estimate_price\n(Nova Pro)"]
            A3["generate_listing\n(Nova Pro)"]
            A4["handle_chat_message\n(Nova Pro)"]
            A5["negotiate_price\n(Nova Pro)"]
            A6["handle_voice_session\n(Nova Sonic)"]
            A7["handle_scheduling\n(Nova Pro)"]
            A8["post_listing_to_marketplace\n(Nova Act)"]
            A9["respond_to_marketplace_chat\n(Nova Act)"]
            A10["upload_image_to_disk"]
        end

        subgraph Services["Services"]
            SVC1["NovaLLM Service\n(OpenRouter gateway)"]
            SVC2["NovaSonic Service\n(Bedrock bidirectional stream)"]
            SVC3["BrowserAutomation Service\n(Nova Act + Playwright)"]
            SVC4["AntiBan Service"]
            SVC5["NotificationService"]
            SVC6["MemoryStore"]
        end
    end

    subgraph AWS["AWS Services"]
        NOVA_LITE["Nova Lite\namazon/nova-lite-v1:0"]
        NOVA_PRO["Nova Pro\namazon/nova-pro-v1:0"]
        NOVA_SONIC["Nova Sonic\namazon.nova-sonic-v1:0\n(Bedrock Bidirectional Stream)"]
        NOVA_ACT["Nova Act\nnova-act-latest\n(Browser Automation SDK)"]
        POLLY["Amazon Polly\n(TTS for voice input)"]
    end

    subgraph Infra["Infrastructure"]
        TEMPORAL["Temporal Server\n(Workflow Orchestration)"]
        REDIS["Redis\n(Rate Limiting / Cache)"]
        PG["PostgreSQL\n(Persistent State)"]
        DISK["Local Disk\n(/data/novasell/images)"]
    end

    subgraph Marketplaces["Marketplaces"]
        SHOZON["Shozon UAE\nshozon.com"]
        DUBIZZLE["Dubizzle\ndubai.dubizzle.com"]
        FB["Facebook Marketplace"]
    end

    U -->|"photo + commands"| CHAT
    CHAT -->|"ACP signals"| ACP
    ACP -->|"task events"| WF
    WF -->|"execute activities"| TEMPORAL
    TEMPORAL --> Activities

    A1 & A2 & A3 & A4 & A5 & A7 --> SVC1
    A6 --> SVC2
    A8 & A9 --> SVC3
    SVC3 --> SVC4

    SVC1 -->|"OpenRouter"| NOVA_LITE & NOVA_PRO
    SVC2 -->|"invoke_model_with_bidirectional_stream"| NOVA_SONIC
    SVC2 --> POLLY
    SVC3 -->|"Nova Act SDK"| NOVA_ACT
    SVC5 --> CHAT

    SVC6 --> REDIS & PG
    A10 --> DISK

    SVC3 --> SHOZON & DUBIZZLE & FB
```

---

### Temporal Worker Architecture

```mermaid
graph LR
    subgraph K8s["Kubernetes Cluster"]
        subgraph ACP_Pod["ACP Pod"]
            ACP_SRV["uvicorn\nproject.acp:acp\n:8000"]
        end

        subgraph Worker_Pod["Temporal Worker Pod"]
            WORKER["python -m project.run_worker"]
            subgraph Reg["Registered"]
                WF_REG["NovaSellStateMachine\n(workflow)"]
                ACT_REG["10 activities"]
            end
        end

        SECRET["K8s Secret: novasell\n─────────────────\nOPENAI_API_KEY\nAWS_ACCESS_KEY_ID\nAWS_SECRET_ACCESS_KEY\nDUBIZZLE_EMAIL / PASS\nSHOZON_EMAIL / PASS / PHONE\nFACEBOOK_EMAIL / PASS / 2FA\nCAPS0LVER_API_KEY\nAGENT_API_KEY"]
    end

    TEMPORAL_SVC["Temporal Frontend\nagentex-temporal-frontend:7233"]
    REDIS_SVC["Redis\nagentex-redis-master:6379"]
    PG_SVC["PostgreSQL\nagentex-postgresql:5432"]

    WORKER -->|"poll task queue\nnovasell-queue"| TEMPORAL_SVC
    ACP_SRV -->|"signal workflow"| TEMPORAL_SVC
    WORKER --> REDIS_SVC & PG_SVC
    SECRET -.->|"envFrom secretRef"| ACP_Pod & Worker_Pod
```

---

## AI Model Stack

```mermaid
graph LR
    subgraph Models["Amazon Nova AI Models"]
        NL["Nova Lite\nnova-lite-v1:0\n──────────────\nMultimodal / Vision\nObject detection\nImage analysis"]
        NP["Nova Pro\nnova-pro-v1:0\n──────────────\nReasoning / Text\nPricing\nListings\nConversation\nNegotiation\nScheduling"]
        NS["Nova Sonic\nnova-sonic-v1:0\n──────────────\nSpeech-to-Speech\nReal-time voice\nBuyer phone calls"]
        NA["Nova Act\nnova-act-latest\n──────────────\nBrowser Automation\nForm filling\nSPA navigation\nCAPTCHA detection"]
    end

    IMG["Product Photo"] -->|"base64 image"| NL
    NL -->|"ObjectAnalysis JSON"| NP
    NP -->|"PriceEstimate JSON"| NP
    NP -->|"ListingContent JSON"| NA
    NA -->|"posts listing"| MP["Marketplace"]
    AUDIO["Buyer Audio\n(PCM 16kHz)"] -->|"bidirectional stream"| NS
    NS -->|"response audio\n(PCM 24kHz)"| BUYER["Buyer Phone"]
    CHAT["Buyer Chat"] -->|"text message"| NP
    NP -->|"ChatResponse"| CHAT
```

| Model | ID | Role | Key Outputs |
|-------|----|------|-------------|
| **Nova Lite** | `amazon/nova-lite-v1:0` | Vision / Multimodal | `ObjectAnalysis` — brand, model, condition score, defects |
| **Nova Pro** | `amazon/nova-pro-v1:0` | Reasoning / Text | Pricing, listings, chat replies, negotiation decisions, scheduling |
| **Nova Sonic** | `amazon.nova-sonic-v1:0` | Speech-to-Speech | Real-time voice call handling via Bedrock bidirectional stream |
| **Nova Act** | `nova-act-latest` | Browser Automation | Navigate SPAs, fill forms, upload images, handle CAPTCHA HITL |

---

## State Machine Flow

```mermaid
stateDiagram-v2
    [*] --> WAITING_FOR_IMAGE : workflow created

    WAITING_FOR_IMAGE --> OBJECT_DETECTION : image uploaded
    WAITING_FOR_IMAGE --> CANCELLED : user cancels

    OBJECT_DETECTION --> PRICING : analysis complete
    OBJECT_DETECTION --> WAITING_FOR_IMAGE : confidence < 50%
    OBJECT_DETECTION --> FAILED : error

    PRICING --> LISTING_GENERATION : price estimated
    PRICING --> FAILED : error

    LISTING_GENERATION --> AWAITING_APPROVAL : listing generated
    LISTING_GENERATION --> FAILED : error

    AWAITING_APPROVAL --> PUBLISHING : user approves
    AWAITING_APPROVAL --> LISTING_GENERATION : user requests edit
    AWAITING_APPROVAL --> AWAITING_APPROVAL : user changes price
    AWAITING_APPROVAL --> CANCELLED : user cancels

    PUBLISHING --> ACTIVE_LISTING : posted successfully
    PUBLISHING --> AWAITING_APPROVAL : posting failed (retry)
    PUBLISHING --> FAILED : max retries exceeded

    ACTIVE_LISTING --> ACTIVE_LISTING : buyer chat / negotiation / voice / scheduling
    ACTIVE_LISTING --> SOLD : seller signals sold
    ACTIVE_LISTING --> COMPLETED : seller removes listing
    ACTIVE_LISTING --> FAILED : critical error

    SOLD --> [*]
    COMPLETED --> [*]
    FAILED --> [*]
    CANCELLED --> [*]
```

---

### State Transition Detail

```mermaid
flowchart TD
    subgraph AL["ACTIVE_LISTING — Event Loop"]
        CHAT_IN["incoming_chat_message signal"]
        OFFER_IN["incoming_buyer_offer signal"]
        VOICE_IN["handle_voice_session signal"]
        SCHED_IN["scheduling request signal"]

        CHAT_IN --> CHAT_ACT["handle_chat_message\n(Nova Pro)"]
        OFFER_IN --> NEG_ACT["negotiate_price\n(Nova Pro)\n+ respond_to_marketplace_chat\n(Nova Act)"]
        VOICE_IN --> VOICE_ACT["handle_voice_session\n(Nova Sonic)\nbidirectional stream"]
        SCHED_IN --> SCHED_ACT["handle_scheduling\n(Nova Pro)"]

        CHAT_ACT & NEG_ACT & VOICE_ACT & SCHED_ACT --> LOOP["wait for next signal"]
        LOOP --> CHAT_IN & OFFER_IN & VOICE_IN & SCHED_IN
    end

    LOOP -->|"sold command"| SOLD
    LOOP -->|"remove command"| COMPLETED
    LOOP -->|"exception"| FAILED
```

---

## Workflow Deep-Dives

### Full Listing Creation Pipeline

```mermaid
sequenceDiagram
    actor Seller
    participant Chat as AgentEx Chat
    participant WF as NovaSellWorkflow
    participant NL as Nova Lite
    participant NP as Nova Pro
    participant NA as Nova Act
    participant MP as Marketplace

    Seller->>Chat: Upload product photo
    Chat->>WF: signal(image_base64)
    WF->>WF: upload_image_to_disk()
    WF->>NL: detect_object(image_base64)
    NL-->>WF: ObjectAnalysis {brand, model, condition=8, defects=[]}

    WF->>NP: estimate_price(object_analysis)
    NP-->>WF: PriceEstimate {recommended=2800 AED, min=2500, trend=stable}

    WF->>NP: generate_listing(analysis, price, hints)
    NP-->>WF: ListingContent {title, description, tags, category}

    WF->>Chat: show listing preview
    Chat->>Seller: "Review your listing — approve / edit / change price / cancel"

    Seller->>Chat: "approve"
    Chat->>WF: signal(approve)

    WF->>NA: post_listing_to_marketplace(listing, price, images, marketplace)

    Note over NA,MP: Nova Act SPA Automation flow (see below)

    NA-->>WF: PostingResult {listing_url, status=posted}
    WF->>Chat: "Listed on Shozon — watching for buyers..."
    WF->>WF: enter ACTIVE_LISTING loop
```

---

### Shozon Marketplace Automation (Nova Act)

```mermaid
flowchart TD
    START["create_shozon_listing()"]

    START --> LOCK["_cleanup_singleton_lock()\nremove stale Chrome lock"]
    LOCK --> BLANK["NovaAct(starting_page='about:blank')"]
    BLANK --> NAV["page.goto(shozon.com)\nwait_until=domcontentloaded\nthen wait networkidle"]
    NAV --> POPUP["nova.act: dismiss welcome popup\n'Get 30% off' modal"]
    POPUP --> LOGIN["_handle_shozon_login_sync()"]

    subgraph LOGIN_FLOW["Login Flow"]
        L1["nova.act: click 'Login/Sign Up'"]
        L2["Playwright: fill email + password\n(bypasses Nova Act guardrails)"]
        L3["hitl_callbacks.ui_takeover()\nUser solves CAPTCHA in browser\n'Live — Click to interact'"]
        L4["wait for 'done' signal"]
        L1 --> L2 --> L3 --> L4
    end

    LOGIN --> LOGIN_FLOW
    LOGIN_FLOW --> POST_AD["nova.act: click 'Post Ad' button\nmax_steps=5"]

    subgraph WIZARD["Ad Creation Wizard — Nova Act Steps (max_steps=25 each)"]
        W1["Screen 1: Click 'Classified'\nin 'What's Your Ad About?' modal"]
        W2["Screen 2: Click 'Continue Manually'\n(not AI-assisted)"]
        W3["Screen 3-6: Category drill-down\nsearch box → multi-level click\nuntil leaf category (no '>' arrow)"]
        W4["Screen 7/9 Page 1:\nFill Title, Description,\nPhone (+971), Location → Next"]
        W5["Screen 10 Page 2:\nUpload image, Condition dropdown → Next"]
        W6["Screen 11: Enter Price → 'Create Ad'"]
        W1 --> W2 --> W3 --> W4 --> W5 --> W6
    end

    POST_AD --> WIZARD
    WIZARD --> RESULT["capture listing_url\nreturn PostingResult{status=posted}"]

    subgraph HEARTBEAT["Background (async task)"]
        HB["_heartbeat_loop()\nevery 30s: activity.heartbeat()\nprevents Temporal retry during HITL"]
    end

    LOGIN_FLOW -.->|"runs concurrently"| HEARTBEAT
```

---

### Buyer Conversation & Negotiation

```mermaid
sequenceDiagram
    actor Buyer
    participant MP as Marketplace Chat
    participant WF as NovaSellWorkflow
    participant NP as Nova Pro
    participant NA as Nova Act

    Buyer->>MP: "Is this still available? Can you do 2200 AED?"
    MP->>WF: signal(incoming_chat_message)

    WF->>NP: handle_chat_message(message, listing, transcript, pricing_boundaries)
    Note over NP: Analyzes intent: negotiation<br/>Offer: 2200 AED (below min: 2500 AED)<br/>Strategy: counter at 2650 AED

    NP-->>WF: ChatResponse {reply, counter_offer=2650, negotiation_status=countered}
    WF->>NA: respond_to_marketplace_chat(marketplace, listing_url, reply)
    NA->>MP: Posts reply via browser automation

    MP->>Buyer: "Thank you for your interest! I can offer 2,650 AED — this is a great deal for the condition."

    Buyer->>MP: "Deal at 2600?"
    MP->>WF: signal(incoming_buyer_offer=2600)

    WF->>NP: negotiate_price(offer=2600, context, pricing_boundaries, history)
    Note over NP: 2600 > min_price (2500) — within 5% discount<br/>Decision: ACCEPT

    NP-->>WF: {decision=accept, agreed_price=2600, reasoning="within acceptable range"}
    WF->>NA: respond_to_marketplace_chat("Great! 2,600 AED it is. When would you like to collect?")
    WF->>WF: update negotiation_status=ACCEPTED
```

---

## Nova Sonic Voice Calls

### Real-Time Voice Architecture

```mermaid
flowchart LR
    subgraph Caller["Buyer Phone Call"]
        PHONE["Caller Audio\nPCM 16kHz 16-bit mono\n(base64)"]
    end

    subgraph Activity["handle_voice_session Activity"]
        SESS["VoiceSession\nstart/continue"]
        PROMPT["_build_system_prompt()\nlisting details + pricing rules"]
    end

    subgraph Stream["_nova_sonic_turn_sync() — ThreadPoolExecutor"]
        direction TB
        EVT1["sessionStart\ninferenceConfig"]
        EVT2["promptStart\nvoice=tiffany\naudioOutput 24kHz PCM"]
        EVT3["contentBlock SYSTEM TEXT\nsystem_prompt"]
        EVT4["contentBlock USER AUDIO\naudio chunks (1KB each)"]
        EVT5["promptStop / sessionEnd"]

        EVT1 --> EVT2 --> EVT3 --> EVT4 --> EVT5
    end

    subgraph Bedrock["AWS Bedrock"]
        NS["amazon.nova-sonic-v1:0\ninvoke_model_with_bidirectional_stream"]
    end

    subgraph Output["Output Events"]
        TXT["contentBlockDelta text\n→ response_text"]
        AUD["contentBlockDelta audioChunk\n→ base64 PCM 24kHz\n→ response_audio_b64"]
    end

    PHONE --> Activity
    PROMPT --> Stream
    Stream -->|"boto3 EventStream"| NS
    NS -->|"response stream"| Output
    TXT & AUD --> RESULT["return {response_text,\nresponse_audio_base64}"]
    RESULT --> PHONE
```

---

### Voice Session Lifecycle

```mermaid
sequenceDiagram
    participant Buyer as Buyer (Phone)
    participant Act as handle_voice_session
    participant Sonic as Nova Sonic (Bedrock)
    participant Polly as Amazon Polly (optional)

    Note over Act: start_session() creates VoiceSession
    Act->>Buyer: Greeting audio (synthesized)

    loop Each Voice Turn
        Buyer->>Act: audio_base64 (PCM 16kHz)
        Act->>Act: _build_system_prompt(listing + pricing)
        Act->>Sonic: sessionStart + promptStart + SYSTEM block + USER audio
        Note over Sonic: Speech understanding + reasoning + speech synthesis
        Sonic-->>Act: text deltas + audio chunks (PCM 24kHz)
        Act->>Buyer: response_audio_base64
        Act->>Act: append to transcript
    end

    Note over Act: end_session() → Nova Pro summary
```

---

## Browser Automation & HITL

### HITL (Human-in-the-Loop) Flow

![HITL — Live browser control: email and CAPTCHA pre-filled by AI, seller types the security code and clicks Login](images/HITL01.png)

> **What you're seeing:** The AI has already filled the email and password automatically. The workflow pauses and hands the browser to the seller — who reads the CAPTCHA image (`5503`), types it in, and clicks **Login**. The red `Live — Click to interact` indicator confirms the browser is live. Once done, the Temporal workflow resumes and continues posting the ad.

```mermaid
flowchart TD
    NOVA_ACT["Nova Act running in browser"]
    CAPTCHA["CAPTCHA / security challenge detected"]
    NOVA_ACT --> CAPTCHA

    CAPTCHA --> CALLBACKS{"hitl_callbacks\ncreated?"}
    CALLBACKS -->|"No (no task_id)"| FALLBACK["nova.act CAPTCHA step\n(best-effort)"]
    CALLBACKS -->|"Yes"| HITL_START

    subgraph HITL_START["HITL Flow"]
        CDP["CDP screenshot\nPage.captureScreenshot\n(no font-loading timeout)"]
        SEND_MSG["send_data_message()\ntype='ui_takeover'\nbrowser screenshot"]
        LIVE["Chat UI shows\n'Live — Click to interact'\nstreaming browser view"]
        USER_INTERACTS["Human:\n1. reads CAPTCHA image\n2. types security code\n3. clicks Login"]
        DONE_SIGNAL["signal('ui_takeover_done')\nworkflow resumes"]

        CDP --> SEND_MSG --> LIVE --> USER_INTERACTS --> DONE_SIGNAL
    end

    HITL_START --> RESUME["Nova Act continues\nwith authenticated session"]
    FALLBACK --> RESUME

    subgraph HEARTBEAT2["Parallel — Temporal Heartbeat"]
        HB2["asyncio.create_task(_heartbeat_loop)\nevery 30s prevents\nactivity timeout during HITL"]
    end

    HITL_START -.->|"concurrent"| HEARTBEAT2
```
### When the agent encounters a CAPTCHA or requires human intervention, NovaSell pauses the Temporal workflow and streams a live browser view directly into the chat UI using Chrome DevTools Protocol (CDP) screenshots. The seller sees a "Live — Click to interact" prompt, takes over the browser for just that moment — solving the CAPTCHA or completing a sensitive action — then hands control back. The Temporal workflow resumes exactly where it left off, with the authenticated session intact. No automation is lost, no state is dropped.
---

### Activity Timeout Configuration

```mermaid
gantt
    title Temporal Activity Timeouts (publishing)
    dateFormat  s
    axisFormat  %Mm %Ss

    section publish activity
    start_to_close_timeout (60 min)   :active, 0, 3600
    heartbeat_timeout (2 min)         :crit, 0, 120

    section typical HITL scenario
    Navigation + Login                :done, 0, 30
    CAPTCHA HITL (user solves)        :active, 30, 300
    Post Ad + Wizard                  :done, 300, 600
```

| Timeout | Value | Why |
|---------|-------|-----|
| `start_to_close_timeout` | **60 min** | Login HITL + full wizard can take 15-45 min |
| `heartbeat_timeout` | **2 min** | Activity must heartbeat every 2 min; loop fires every 30s (4× margin) |
| `maximum_attempts` | **1** | HITL cannot be automatically retried |

---

## Anti-Ban Strategy

```mermaid
flowchart LR
    subgraph AntiBan["AntiBanService"]
        RD["random_delay_sync()\n1-3s between actions"]
        TD["typing_delay_sync()\n0.05-0.15s per character"]
        PL["page_load_delay_sync()\n2s after navigation"]
        RL["check_listing_rate_limit()\n≤3/hour, ≤10/day\n10min cooldown between posts"]
        SESSION["get_user_data_dir()\npersistent browser profile\ncookie + session reuse"]
        FP["Browser fingerprint\n1280×720 viewport\ncustom user-agent"]
    end

    subgraph Lock["SingletonLock Recovery"]
        CLEAN["_cleanup_singleton_lock()\nremoves stale SingletonLock\nSingletonSocket\nSingletonCookie\nbefore Nova Act launch"]
    end

    BROWSER["Nova Act Browser"] --> AntiBan
    AntiBan --> MARKETPLACE["Marketplace\n(no bot detection)"]
    CRASH["Previous crash\nstale lock file"] --> Lock --> BROWSER
```

---

## Data Models

### Listing Domain Models

```mermaid
classDiagram
    class ObjectAnalysis {
        +str object_type
        +str brand
        +str model
        +float condition_score
        +List~str~ visible_defects
        +str color
        +List~str~ accessories
        +float confidence
    }

    class PriceEstimate {
        +float min_price
        +float max_price
        +float recommended_price
        +float original_retail_price
        +float depreciation_percent
        +List~ComparableItem~ comparable_items
        +PriceTrend price_trend
        +SellSpeed sell_speed_estimate
        +float confidence
    }

    class ComparableItem {
        +str title
        +float price
        +str platform
        +str condition
        +str url
    }

    class ListingContent {
        +str title
        +str description
        +str short_description
        +List~str~ tags
        +str category
        +str subcategory
        +List~str~ highlights
        +Dict specifications
        +List~str~ seo_keywords
    }

    class PostingResult {
        +str marketplace
        +str listing_url
        +str listing_id
        +str status
        +List~str~ screenshots
        +List~Dict~ automation_steps
        +str error_message
    }

    class Listing {
        +str listing_id
        +ListingStatus status
        +str image_file_path
        +ObjectAnalysis object_analysis
        +PriceEstimate price_estimate
        +ListingContent listing_content
        +PostingResult posting_result
        +float min_acceptable_price
        +float max_discount_percentage
    }

    Listing *-- ObjectAnalysis
    Listing *-- PriceEstimate
    Listing *-- ListingContent
    Listing *-- PostingResult
    PriceEstimate *-- ComparableItem
```

---

### Conversation Domain Models

```mermaid
classDiagram
    class NovaSellData {
        +str task_id
        +str target_marketplace
        +str image_base64
        +str image_file_path
        +ObjectAnalysis object_analysis
        +PriceEstimate price_estimate
        +ListingContent listing_content
        +List~PostingResult~ posting_results
        +bool approved_by_user
        +List~ChatMessage~ chat_history
        +List~VoiceSession~ voice_sessions
        +List~NegotiationContext~ negotiation_contexts
        +List~HITLRequest~ hitl_requests
        +float min_acceptable_price
        +float max_discount_percentage
    }

    class ChatMessage {
        +str role
        +str content
        +datetime timestamp
        +ConversationChannel channel
    }

    class NegotiationContext {
        +str listing_id
        +str buyer_id
        +float listed_price
        +float min_acceptable_price
        +float max_discount_percentage
        +List~NegotiationRound~ rounds
        +NegotiationStatus current_status
        +float final_agreed_price
    }

    class NegotiationRound {
        +int round_number
        +float buyer_offer
        +float agent_counter
        +NegotiationStatus status
        +str reasoning
    }

    class VoiceSession {
        +str session_id
        +str status
        +List~Dict~ transcript
        +str summary
        +str caller_phone
        +str negotiation_result
    }

    class HITLRequest {
        +str request_id
        +HITLAction action
        +str reason
        +Dict context
        +str status
        +str resolution
    }

    NovaSellData *-- ChatMessage
    NovaSellData *-- NegotiationContext
    NovaSellData *-- VoiceSession
    NovaSellData *-- HITLRequest
    NegotiationContext *-- NegotiationRound
```

---

## Services

```mermaid
graph TB
    subgraph Services["Service Layer"]
        LLM["NovaLLM Service\nnova_llm.py\n────────────────\ncall_nova_pro(messages)\ncall_nova_lite_vision(image)\nparse_json_response()\nOpenRouter gateway\nRetry with backoff"]

        SONIC["NovaSonic Service\nnova_sonic.py\n────────────────\nstart_session()\nprocess_audio_turn()\nend_session()\nBedrock bidirectional stream\nThreadPoolExecutor"]

        BROWSER["BrowserAutomation Service\nbrowser_automation.py\n────────────────\ncreate_shozon_listing()\ncreate_dubizzle_listing()\ncreate_facebook_listing()\n_handle_shozon_login_sync()\n_create_hitl_callbacks()\n_send_data_message()\n_cleanup_singleton_lock()"]

        ANTIBAN["AntiBan Service\nanti_ban.py\n────────────────\nrandom_delay_sync()\ntyping_delay_sync()\ncheck_listing_rate_limit()\nrecord_listing_created()\nget_user_data_dir()"]

        NOTIFY["Notification Service\nnotification_service.py\n────────────────\nnotify_hitl_required()\nnotify_listing_posted()\nnotify_negotiation_update()\nSlack webhook + SMTP"]

        MEMORY["Memory Store\nmemory_store.py\n────────────────\nstore/get listing\nstore/get conversation\nRates in Redis\nState in PostgreSQL"]
    end

    BROWSER --> ANTIBAN
    BROWSER --> NOTIFY
    LLM & SONIC & BROWSER --> MEMORY
```

---

## Activities Reference

| Activity | Model | Timeout | Description |
|----------|-------|---------|-------------|
| `detect_object` | Nova Lite | 5 min | Multimodal image analysis → `ObjectAnalysis` |
| `estimate_price` | Nova Pro | 5 min | Dubai market valuation → `PriceEstimate` |
| `generate_listing` | Nova Pro | 5 min | SEO-optimized listing copy → `ListingContent` |
| `handle_chat_message` | Nova Pro | 3 min | Buyer Q&A, intent classification → `ChatResponse` |
| `negotiate_price` | Nova Pro | 3 min | Strategic price counter → accept/decline/counter |
| `handle_voice_session` | Nova Sonic | 10 min | Real-time voice call → `{response_text, audio_b64}` |
| `handle_scheduling` | Nova Pro | 3 min | Parse availability, confirm pickup time |
| `post_listing_to_marketplace` | Nova Act | **60 min** | Browser automation + HITL → `PostingResult` |
| `respond_to_marketplace_chat` | Nova Act | 5 min | Post reply via browser on marketplace |
| `upload_image_to_disk` | — | 1 min | Save base64 image to `/data/novasell/images/YYYY/MM/DD/` |

---

## Configuration Reference

### Environment Variables

| Variable | Default | Required | Description |
|----------|---------|----------|-------------|
| `OPENAI_API_KEY` | — | **Yes** | OpenRouter / LiteLLM gateway key |
| `OPENAI_BASE_URL` | `https://openrouter.ai/api/v1` | Yes | LLM gateway endpoint |
| `AWS_REGION` | `us-east-1` | **Yes** | AWS region (Nova Sonic requires us-east-1) |
| `AWS_ACCESS_KEY_ID` | — | **Yes** | AWS access key |
| `AWS_SECRET_ACCESS_KEY` | — | **Yes** | AWS secret key |
| `TEMPORAL_ADDRESS` | `localhost:7233` | **Yes** | Temporal frontend address |
| `DUBIZZLE_EMAIL` | — | For Dubizzle | Dubizzle account email |
| `DUBIZZLE_PASS` | — | For Dubizzle | Dubizzle account password |
| `SHOZON_EMAIL` | — | For Shozon | Shozon account email |
| `SHOZON_PASS` | — | For Shozon | Shozon account password |
| `SHOZON_PHONE` | — | For Shozon | Phone number for Shozon ads (+971…) |
| `FACEBOOK_EMAIL` | — | For FB | Facebook account email |
| `FACEBOOK_PASS` | — | For FB | Facebook account password |
| `FACEBOOK_2FA_SECRET` | — | For FB | 2FA TOTP secret |
| `CAPSOLVER_API_KEY` | — | For FB | CapSolver API key for FB CAPTCHA |
| `NOVA_ACT_USER_DATA_DIR` | `/data/novasell/nova-act-profile` | No | Persistent browser profile |
| `IMAGE_STORAGE_DIR` | `/data/novasell/images` | No | Local image storage path |
| `REDIS_URL` | `redis://localhost:6379/0` | No | Redis for rate limiting |
| `DATABASE_URL` | `postgresql+asyncpg://…` | No | PostgreSQL for persistent state |
| `SLACK_WEBHOOK_URL` | — | No | Slack HITL notification webhook |
| `ALLOWED_EMAILS` | — | No | Comma-separated authorized seller emails |
| `MIN_ACTION_DELAY` | `1.0` | No | Min browser action delay (seconds) |
| `MAX_ACTION_DELAY` | `3.0` | No | Max browser action delay (seconds) |
| `MAX_LISTINGS_PER_HOUR` | `3` | No | Rate limit: listings per hour |
| `MAX_LISTINGS_PER_DAY` | `10` | No | Rate limit: listings per day |
| `NOVA_ACT_MODEL_ID` | `nova-act-latest` | No | Nova Act model version |
| `NOVA_SONIC_MODEL` | `amazon/nova-sonic-v1:0` | No | Nova Sonic model ID |

---

## Project Structure

```
agents/novasell/
├── Dockerfile
├── pyproject.toml
├── nova_sonic_sim.py              # Standalone Nova Sonic simulation script
├── .env.example
│
├── chart/novasell/                # Helm chart
│   ├── Chart.yaml
│   └── values.yaml               # K8s deployment config + secrets
│
└── project/
    ├── __init__.py
    ├── acp.py                     # ACP server (FastAPI entry point :8000)
    ├── config.py                  # Centralized config (pydantic-settings)
    ├── constants.py               # System prompts for all Nova models
    ├── activities.py              # 10 Temporal activities (agent entry points)
    ├── workflow.py                # Main NovaSellWorkflow (signal handler + state machine runner)
    ├── run_worker.py              # Temporal worker entry point
    │
    ├── models/
    │   ├── listing.py             # ObjectAnalysis, PriceEstimate, ListingContent, PostingResult, Listing
    │   └── conversation.py        # ChatMessage, NegotiationContext, VoiceSession, HITLRequest, BuyerProfile
    │
    ├── services/
    │   ├── nova_llm.py            # Nova Pro/Lite via OpenRouter (async + multimodal)
    │   ├── nova_sonic.py          # Nova Sonic bidirectional streaming voice service
    │   ├── browser_automation.py  # Nova Act automation for Shozon / Dubizzle / Facebook
    │   ├── anti_ban.py            # Delays, rate limiting, browser fingerprinting
    │   ├── memory_store.py        # Redis + PostgreSQL state management
    │   └── notification_service.py # Slack / email HITL alerts
    │
    ├── state_machines/
    │   └── novasell_agent.py      # NovaSellState enum, NovaSellData, NovaSellStateMachine
    │
    └── workflows/
        ├── terminal_states.py     # SoldWorkflow, CompletedWorkflow, FailedWorkflow, CancelledWorkflow
        └── sell/
            ├── waiting_for_image.py   # State: wait for photo upload
            ├── object_detection.py    # State: Nova Lite image analysis
            ├── pricing.py             # State: Nova Pro market pricing
            ├── listing_generation.py  # State: Nova Pro listing copywriting
            ├── awaiting_approval.py   # State: human review & approval gate
            ├── publishing.py          # State: Nova Act marketplace posting
            └── active_listing.py      # State: live listing management loop
```

---

## Deployment

### Helm Chart

```yaml
# agents/novasell/chart/novasell/values.yaml (key values)

service:
  replicas: 1
  image: eslamanwar/rilo:agents-novasell-v0.1
  containerPort: 8000
  command: ["uvicorn", "project.acp:acp", "--host", "0.0.0.0", "--port", "8000"]
  resources:
    requests: { cpu: 250m, memory: 250Mi }
    limits:   { cpu: 1000m, memory: 2Gi }

temporal-worker:
  enabled: true
  command: python
  args: ["-m", "project.run_worker"]
  # polls task queue: novasell-queue
```

Secrets are read from a K8s secret named **`novasell`**:

```bash
kubectl create secret generic novasell \
  --from-literal=OPENAI_API_KEY=... \
  --from-literal=AWS_ACCESS_KEY_ID=... \
  --from-literal=AWS_SECRET_ACCESS_KEY=... \
  --from-literal=DUBIZZLE_EMAIL=... --from-literal=DUBIZZLE_PASS=... \
  --from-literal=SHOZON_EMAIL=...   --from-literal=SHOZON_PASS=... \
  --from-literal=SHOZON_PHONE=...   \
  --from-literal=FACEBOOK_EMAIL=... --from-literal=FACEBOOK_PASS=... \
  --from-literal=AGENT_API_KEY=...
```

---

## Getting Started

### Local Development

```bash
# 1. Install dependencies
cd agents/novasell
uv sync

# 2. Configure environment
cp .env.example .env
# Edit .env with your credentials

# 3. Start Temporal (Docker)
docker run --rm -p 7233:7233 temporalio/auto-setup:latest

# 4. Start the ACP server
uvicorn project.acp:acp --host 0.0.0.0 --port 8000 --reload

# 5. Start the Temporal worker (separate terminal)
python -m project.run_worker
```

### Test Nova Sonic Locally

```bash
# Run the standalone simulation (uses Amazon Polly for TTS input)
python nova_sonic_sim.py

# Play the response audio
ffplay -f s16le -ar 24000 -ac 1 /tmp/nova_sonic_response.raw
```

### Helm Deploy

```bash
helm upgrade --install novasell ./chart/novasell \
  --namespace default \
  --set service.image.tag=agents-novasell-v0.1
```

---

## Technology Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Language | Python | 3.12 |
| AI — Vision | Amazon Nova Lite | nova-lite-v1:0 |
| AI — Reasoning | Amazon Nova Pro | nova-pro-v1:0 |
| AI — Voice | Amazon Nova Sonic | nova-sonic-v1:0 |
| AI — Browser | Amazon Nova Act | nova-act-latest |
| Orchestration | Temporal.io | state machine workflows |
| API Server | FastAPI (AgentEx ACP) | — |
| Browser | Playwright (via Nova Act) | — |
| LLM Gateway | OpenRouter / LiteLLM | OpenAI-compatible |
| Voice Streaming | AWS Bedrock bidirectional stream | boto3 ≥ 1.35 |
| Config | pydantic-settings | env vars |
| State | In-memory + Redis + PostgreSQL | — |
| Notifications | Slack webhooks + SMTP | — |
| Container | Docker + Helm | Kubernetes |
