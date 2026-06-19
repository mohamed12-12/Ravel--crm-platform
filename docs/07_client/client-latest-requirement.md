# Client Requirements Confirmation (Updated)

Date: 2026-06-16

## Project Status

Current system status:

* Pytest: 48/48 Passed
* npm test: Passed
* npm build: Passed
* npm typecheck: Passed
* Python compileall: Passed

Current baseline is considered stable and should remain the reference point for future development.

---

# Approved Client Requirements

## 1. Agent Persona

### Requirement

The AI assistant should be called:

**Rahvel Agent**

### Behavior

* Automatically detect customer language.
* Respond in the same language whenever possible.
* Support Arabic and English.
* Persona configuration should remain editable from configuration files or CRM settings in the future.

### Phase

Demo Phase

---

## 2. Numbered Choices Everywhere

### Requirement

All customer options should support:

* Number selection
* Text selection

### Example

Flight Option:

1. With Flight
2. Without Flight

Accepted responses:

* 1
* 2
* With Flight
* Without Flight
* Yes
* No
* Arabic equivalents

### Phase

Demo Phase

---

## 3. Post-Trip Human Handoff

### Requirement

Customers can be transferred to a human after a trip is completed if additional support is needed.

### Client Decision

The handoff logic must be configurable by Rahma Travel employees.

### Implementation Rule

Do not hardcode business rules.

Allow configuration such as:

* Enable/Disable post-trip handoff
* Trigger keywords
* Trigger intents
* Responsible employee

### Phase

Demo Phase

---

## 4. Flight Awareness

### Requirement

The agent should understand:

* Trips may include flights.
* Trips may exclude flights.
* Most trips are currently offered without flights.

### Phase

Demo Phase

---

## 5. VIP and Group Discounts

### Requirement

Discount information should come from:

* Trip Notes
* CRM Trip Information

### Important

No hardcoded formulas.

The system should read available discount information and communicate it.

### Future

Actual pricing calculations can be added later if needed.

### Phase

Demo Phase

---

## 6. Traveler ID and Trip ID Policy

### Traveler ID

Must always be preserved.

### Trip ID

May be regenerated or modified when required.

### Requirement

Implementation must remain backward compatible.

Historical links must not break.

### Phase

Requires implementation planning.

---

## 7. Passport Collection

### Requirement

International bookings require passport information.

### Client Decision

Passport files/images may be uploaded.

### Required Fields (Initial Version)

* Full Name
* Passport Number
* Nationality
* Expiry Date
* Passport Image

Additional fields may be added later.

### Phase

Demo Phase

---

## 8. Attachments

### Requirement

Customers may upload:

* JPG
* JPEG
* PNG
* PDF

### Storage

Files should be associated with:

* Traveler Profile
* CRM Customer Record

### Recommended Limits

* Maximum Size: 10 MB

### Phase

Demo Phase

---

## 9. AI to Human Handoff

### Requirement

The CRM should support:

* AI handling
* Human takeover
* Handoff tracking

### Current State

Basic handoff already exists.

### Goal

Extend existing handoff functionality.

### Phase

Demo Phase

---

## 10. Website Link

### Requirement

Agent should share company website when available.

### Current Status

Client has not yet provided official URL.

### Temporary Behavior

Agent should respond:

"The website link is not available yet. A human agent can assist you."

### Phase

Demo Phase

---

## 11. Contact Page Evidence

### Requirement

Provide screenshot evidence showing:

* Customer conversation
* Human handoff
* Human response

### Current Status

Environment not specified.

### Recommendation

Generate evidence from demo environment after implementation.

### Phase

Demo Validation

---

## 12. Visa Information

### Requirement

Agent should answer:

* Visa required
* Visa not required

### Current Scope

Demo implementation only.

### Rule

If information is unavailable:

Escalate to human agent.

### Required Disclaimer

"Visa requirements may change. Please confirm with the embassy or official authority before making travel decisions."

### Production Restriction

No live visa web automation yet.

### Phase

Demo Phase

---

# Not Included In Current Scope

The following remain outside this implementation phase:

## Real Instagram Integration

Not included:

* Meta Webhooks
* Production Tokens
* Signature Validation
* Retry Queues
* Monitoring
* Audit Logs
* Rate Limiting

These belong to a dedicated Instagram Integration Phase.

---

## Live Visa Automation

Not included:

* Live government lookup
* Real-time web crawling
* Automated embassy checks

Requires separate compliance review.

---

# Risks

## Medium

* Passport storage introduces sensitive personal data.
* Attachments require validation and access control.
* Trip ID regeneration may affect relationships if not planned carefully.

## High

* Future Instagram production integration.
* Future live visa automation.

---

# Recommended Implementation Order

## Phase 1 (Immediate)

* Rahvel Agent persona
* Language detection
* Numbered choices
* Flight-awareness messaging
* Website-link intent
* Discount information from trip notes

## Phase 2

* Passport collection
* Attachment uploads
* Extended CRM profile support

## Phase 3

* Configurable post-trip handoff
* Enhanced AI-human collaboration

## Phase 4

* Instagram production integration

## Phase 5

* Visa automation
* Production hardening
* Compliance and security controls

---

# Final Recommendation

Project is approved to begin Demo Phase implementation.

Real Instagram integration should remain a separate project phase after all demo features are validated and accepted.
