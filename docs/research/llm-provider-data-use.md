# AI provider data-use terms: who trains on API inputs

Supporting asset for [ADR 0003](../adr/0003-tmdb-licensing-posture.md) (TMDB licensing posture) and wayfinder ticket [#15](https://github.com/KyleKDang/anchor/issues/15).
Question answered: does each provider use customer API inputs/outputs to train models by default, and what retention/opt-out exists?
All findings verified 2026-08-02 against live first-party pages; the two most load-bearing quotes (Anthropic commercial terms, Gemini terms) were re-fetched with character-for-character verification.
Terms move; re-verify at integration time.

## Summary

| Provider | Trains on API inputs by default | Notes |
| --- | --- | --- |
| Anthropic API | No | Contractually prohibited; 30-day retention; ZDR available |
| OpenAI API (incl. embeddings) | No | Explicit opt-in required; 30-day abuse-monitoring retention; ZDR available |
| Google Gemini API | Free tier: yes. Paid tier: no | Free tier includes human review |
| Voyage AI | Yes | Perpetual training license unless opted out; opt-out prospective-only |

Under ADR 0003's no-training provider rule: Anthropic API, OpenAI API, and Gemini paid tier are allowed; Gemini free tier and default-configured Voyage AI are barred.

## 1. Anthropic (Claude API / Commercial Terms)

**Source:** Commercial Terms of Service, effective June 17, 2025 - https://www.anthropic.com/legal/commercial-terms

Verbatim (Section B, Customer Content; standalone sentence with no exception clause):

> "Anthropic may not train models on Customer Content from Services."

**Retention:** Anthropic Privacy Center, "How long do you store my organization's data?" - https://privacy.claude.com/en/articles/7996866-how-long-do-you-store-my-organization-s-data

> "For Anthropic API users, we automatically delete inputs and outputs on our backend within 30 days of receipt or generation"

...except "When you and we have agreed otherwise (e.g. zero data retention agreement)".
Zero data retention exists and is applied per-organization via the Sales team; it covers eligible Anthropic APIs and API-key-based products (https://privacy.claude.com/en/articles/8956058-i-have-a-zero-data-retention-agreement-with-anthropic-what-products-does-it-apply-to).
Even under ZDR, data may be retained "to comply with law or combat misuse", and safety-classifier results are retained.

**Consumer distinction (Claude.ai apps, NOT the API):** the consumer policy differs materially.
Privacy Policy effective July 8, 2026 - https://www.anthropic.com/legal/privacy

> "We may use your Inputs and Outputs to train and improve Anthropic AI models, unless you opt out through your account settings."

Consumer chats are used for training on an opt-out basis, and safety-flagged conversations are used even if opted out.
This does not apply to the commercial API.

**Bottom line: trains on API inputs by default: no** (contractually prohibited; 30-day default retention; ZDR available).
Consumer Claude.ai: yes by default, opt-out via settings.

## 2. OpenAI API (including embeddings)

**Source:** "Your data" guide, OpenAI API platform docs - https://platform.openai.com/docs/guides/your-data (301-redirects to https://developers.openai.com/api/docs/guides/your-data; content fetched from the latter)

Verbatim:

> "data sent to the OpenAI API is not used to train or improve OpenAI models (unless you explicitly opt in to share data with us)."

**Retention:**

> "abuse monitoring logs are generated for all API feature usage and retained for up to 30 days"

**Embeddings specifics:** the `/v1/embeddings` endpoint is listed in the guide's endpoint table with 30-day abuse-monitoring retention by default and marked eligible for Zero Data Retention ("Yes", with "None" retention when ZDR is enabled), so embeddings inputs are excluded from abuse logs entirely under ZDR.

Note: the corroborating page https://openai.com/enterprise-privacy returned HTTP 403 to automated fetching, so its wording is **unverified**; the quotes above are from the fetched first-party API docs page only.

**Bottom line: trains on API inputs by default: no** (explicit opt-in required; 30-day abuse-monitoring retention; ZDR available including for embeddings).

## 3. Google Gemini API

**Source:** Gemini API Additional Terms of Service, last updated April 28, 2026 - https://ai.google.dev/gemini-api/terms (both quotes below confirmed character-for-character)

**Unpaid Services (free tier)** - verbatim:

> "When you use Unpaid Services, including, for example, Google AI Studio and the unpaid quota on Gemini API, Google uses the content you submit to the Services and any generated responses to provide, improve, and develop Google products and services and machine learning technologies, including Google's enterprise features, products, and services, consistent with our Privacy Policy."

> "To help with quality and improve our products, human reviewers may read, annotate, and process your API input and output."

The terms also warn: "Do not submit sensitive, confidential, or personal information to the Unpaid Services."

**Paid Services** - verbatim:

> "When you use Paid Services, including, for example, the paid quota of the Gemini API, Google doesn't use your prompts (including associated system instructions, cached content, and files such as images, videos, or documents) or responses to improve our products..."

**Retention (paid):**

> "For Paid Services, Google logs prompts and responses for a limited period of time, solely for detecting and preventing violations of the Prohibited Use Policy to maintain the safety and security of the Services, and any required legal or regulatory disclosures."

No fixed retention number is stated in the terms ("a limited period of time").
Users in the EEA, Switzerland, or the UK are served under the paid-services data terms regardless of tier (per the same page).
No opt-out exists on the free tier - the "opt-out" is paying (paid quota routes to the no-training terms and Google's Data Processing Addendum).

**Bottom line: trains on API inputs by default: yes on the free tier (including human review), no on the paid tier.**

## 4. Voyage AI (embeddings)

**Source:** Voyage AI Privacy Policy, updated February 20, 2025 - https://www.voyageai.com/privacy

Verbatim:

> "unless you 'opt out' as described below, you grant Voyage AI (and its successors and assigns) a worldwide, irrevocable, perpetual, royalty-free, fully paid-up, right and license to use, copy, reproduce, distribute, prepare derivative works of, display and perform the Customer Content"

The license expressly includes use "to train, improve, and otherwise further develop the Service (such as by training the artificial intelligence models we use)."

Opt-out mechanics (same page):

> "You may opt out of our use rights in Section 3(iii) above via the opt-out functionality on the Website. If you choose to opt out, it will apply only to Customer Content you submit after the time at which you out opt."

> "If you opt out, your Customer Content will be immediately deleted by Voyage AI after it is processed for you."

**Source 2:** Voyage AI docs FAQ - https://docs.voyageai.com/docs/faq - verbatim:

> "For Voyage-hosted model API endpoints, customers can opt-out from Voyage storing and using their data for future model training so that there is a zero-day retention of the data."

Opt-out requires being an organization Admin with a payment method on file (dashboard: Organization > Terms of Service > toggle "Opted In" to "Opted Out"), and the FAQ notes: "You won't be able to opt-in again in the dashboard after you opt out."
The opt-out is prospective only - previously submitted data remains under the training license.

**Unverified side note:** search results surfaced a MongoDB docs page (mongodb.com/docs/voyageai) stating Voyage AI does not train on customer data from the MongoDB Atlas integration "at this time"; that page was not fetched directly, so treat as unverified.

**Bottom line: trains on API inputs by default: yes** (perpetual training license unless actively opted out; opt-out gives zero-day retention but is prospective-only and effectively irreversible via the dashboard).
The outlier of the four providers.
