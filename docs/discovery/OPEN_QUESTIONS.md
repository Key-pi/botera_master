# Open Questions

Status: discovery draft, not approved.

## Product Problem And Users

- What is the minimal bundle of features that makes the product feel like it "works as expected" for a solo beauty master and their clients?
- What makes a user choose BoteraMaster instead of Google Calendar, Telegram notes, existing booking systems, or manual messages?
- How will the product problem be validated before or during MVP, given that the product origin is TableOS analogy and Botera ecosystem strategy rather than a concrete observed master scenario?

## Roles And Tenant Boundaries

- What exact permissions does the MVP owner/master role have?
- Product language is confirmed: clients see "master", platform/back-office and internal documentation/code use `partner`.
- Can the same Telegram user interact with multiple masters while their history, notes, and partner-specific client data remain separate per partner?
- Which TableOS partner/bot patterns are relevant for BoteraMaster, and which are specific to hospitality and should not be copied?

## MVP Scope

- Is the proposed broad MVP boundary approved as-is, or should it be cut before final specification?
- Are the proposed outside-MVP items approved as-is?
- What exact technical broadcast pacing/rate-limit strategy is required for Telegram and multiple active bots?
- Are broadcast audit events needed at all in MVP, and if yes, which ones?
- What validation/recovery is required when a partner changes their own bot token?
- For multilingual partner-edited text/button overrides, should the admin UI show all languages at once or edit one language at a time?

## Data And Lifecycle

- What are the core entities in MVP?
- Which cancelled/no-show records are shown in client history, and with what labels/details?
- How is an appointment total calculated when service price, accumulated penalties, discounts, or manual adjustments are present?

## Money

- What precision is required for money calculations?
- Are discounts, promo codes, or loyalty balances part of MVP?
- Should no-show penalties, if added after MVP, be shown together with cancellation penalties or as a separate report line?
- For post-MVP manual prepayment by requisites, how should it be marked: requested, sent, confirmed, rejected, expired?
- For post-MVP manual prepayment, should the client send proof through the bot and the master approve it with inline buttons?

## Integrations

- Which external systems are required at MVP?
- Telegram is required at launch. Are any other integrations required for MVP?
- Is Google Calendar sync required or future-only?
- Are Instagram, WhatsApp, SMS, email, or payment systems in scope?

## Operations, Safety, And Reliability

- What should happen when Telegram delivery fails?
- What should happen if client-to-master bot-mediated messaging fails?
- How should conversation context be preserved when master and client exchange bot-mediated messages?
- What recovery flow is needed if reminder sending fails?
- What should happen if a partner misconfigures required bot texts, button labels, confirmation behavior, reminder timings, or phone requirements?
- What should happen if a partner misconfigures required booking fields?
- What should happen if multilingual custom content is missing or invalid for one of the supported languages?
- How should sensitive data be protected in product UX, storage, logs, and exports?
- Which exact internal audit/diagnostic events are platform-only?

## Readiness

- What business metrics define success for the first release?
- What future directions should be preserved without being built now?
- How much TableOS bot/content/settings architecture should be reused conceptually, and where should BoteraMaster deliberately diverge?
- How should the future Botera ecosystem layer, including cross-product promotions, bonuses, and partner programs, stay separate from BoteraMaster MVP?
