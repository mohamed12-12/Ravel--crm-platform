# Rahma Traveler V2: High-Level Architecture Plan

This document outlines the professional upgrade path for the Rahma Traveler CRM, transitioning from a prototype into a deterministic, production-ready system.

---

## 🏗️ Core Architecture Overview
The system will shift to a **Hybrid Middleware** approach:
- **Database**: PostgreSQL with Prisma ORM (Strict schema enforcement).
- **API Layer**: Express/TypeScript (High-concurrency DM handling).
- **Validation**: Zod (Ensuring no garbage data enters the sheets).
- **AI Integration**: Gemini is used for *reasoning*, but NOT for *text generation*.

---

## 🛠️ Module 1: The Deterministic "Copy Guard"
**Goal**: Prevent the AI from making up prices, dates, or promises (Zero Hallucination).

### 1. The Schema (CopyLibrary)
| Key | Context | Arabic (ar_text) | English (en_text) |
|-----|---------|------------------|-------------------|
| `TRIP_INFO` | Recommendation | رحلتنا إلى {trip_name} تبدأ من {price} | Our trip to {trip_name} starts at {price} |
| `BOOKING_DRAFT` | Confirmation | تم إنشاء حجز مبدئي رقم {id} | A draft booking {id} has been created |

### 2. The Logic
1. AI Agent analyzes user message.
2. AI Agent returns a **Template ID** (e.g., `TRIP_INFO`) + **Variables** (e.g., `{price: 5000}`).
3. The **Orchestrator** fetches the approved text from the library.
4. The system sends the approved text. **The AI never writes the final message.**

---

## 🛠️ Module 2: The Identity Merge Dashboard
**Goal**: Handle families or groups using a single phone number without breaking data integrity.

### 1. The Strategy: "Master & Aliases"
- We create a **MasterAccount** for the primary phone number.
- Multiple **Traveler** profiles (Husband, Wife, Child) are linked to this Master.
- **Sync Logic**: When the Master's status changes, all linked profiles are checked for consistency.

### 2. The API (`POST /api/crm/resolve-identity`)
```typescript
{
  "masterTravelerId": "TR001",
  "linkTravelerIds": ["TR045", "TR089"],
  "operation": "DEDUPLICATE_ALIAS"
}
```

---

## 🛠️ Module 3: Omnichannel Handoff Engine
**Goal**: Stop the bot and call a human when things get complicated.

### 1. Automatic Triggers
- `DUPLICATE_PHONE`: Multiple people found for one number (Handoff for merge).
- `BLOCKED`: User expressed frustration or requested a human.
- `CUSTOM_REQUEST`: User asked for a "Private Trip" (Logic not in system).

### 2. The Workflow
1. **Event**: AI triggers `handoff_required: true`.
2. **Action**: The system sets `automation_active: false` in Redis for that User ID.
3. **Notification**: A real-time alert (WebSocket) hits the Admin Dashboard.
4. **Resume**: Human clicks "Resume Automation" after fixing the issue.

---

## 🚀 Execution Roadmap
1. **Database Setup**: Deploy PostgreSQL and generate Prisma client.
2. **The Firewall**: Implement the Express middleware that validates AI output against the `CopyLibrary`.
3. **The Dashboard**: Create the React/Next.js interface for Identity Merging and Handoff management.

---
**Status**: Ready for Implementation.
**Architect**: Antigravity (Senior Full Stack)
