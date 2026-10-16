# RVD: Minimal, Production-Safe Duitku QRIS Redirect Flow

## 0. Mandatory Instructions

Project: `rvd-2nd` — Raja Voting Digital.

The existing Duitku integration is already implemented. This task is a targeted modification of the current payment flow, not a new payment integration.

**Primary objective:** When the customer clicks the existing "Lanjutkan pembayaran" button, navigate directly to the valid Duitku payment URL already associated with that transaction. Payment verification, reconciliation, status transitions, and vote allocation remain backend responsibilities.

### Non-negotiable constraints

1. Inspect the actual repository and current implementation before editing anything.
2. Start from the current checked-out branch and working tree. Do not assume a branch, commit, frontend framework, endpoint, or component name without inspecting the repository.
3. Preserve existing working checkout, payment creation, callback, database, and voting behavior.
4. Make the smallest changes that satisfy this specification.
5. Do not replace working functions with new implementations merely to simplify the code.
6. Do not invent endpoints, database columns, response fields, provider URLs, environment variables, or business rules.
7. Reuse existing services, models, repositories, route handlers, UI components, and settlement logic.
8. Do not create a second payment flow alongside the existing one.
9. Do not change the database schema unless a demonstrated technical requirement makes it unavoidable. Explain and justify any proposed schema change before applying it.
10. Do not deploy, alter production data, create real payments during automated tests, or expose secrets.
11. Do not stop after writing a plan. Implement the required changes and run the relevant tests where possible.
12. If the existing code or verified provider response does not support a requirement, report the exact blocker instead of fabricating a workaround.

## 1. Establish the Existing Implementation

Before modifying code, trace the real execution path for:

- Customer checkout and payment creation.
- Persistence of the internal payment record.
- Storage and retrieval of the Duitku `paymentUrl`.
- The existing "Lanjutkan pembayaran" button.
- The current intermediate payment page, including any "Buka pembayaran Duitku" and "Periksa status" buttons.
- The existing Duitku callback endpoint.
- The current transaction-status reconciliation service.
- Payment settlement and vote allocation.
- The Duitku `returnUrl` configuration.

Inspect the actual source files and follow the function calls. Do not rely solely on previous summaries, comments, or assumed file names.

Identify the exact frontend component or template and the exact backend service or endpoint involved. Record the current behavior before editing.

The existing project previously reported commit `31a0b1e`, which included Duitku timeout and transaction-status changes. Verify the current source instead of assuming that commit or its behavior is present.

## 2. Required Customer Payment Flow

Implement this exact user journey using the existing application structure:

1. Customer completes the existing checkout process.
2. Backend creates or retrieves the payment using the existing idempotency rules.
3. Backend persists the payment data using the existing database logic.
4. The existing "Lanjutkan pembayaran" button navigates directly to the stored, valid Duitku `payment_url`.
5. Customer completes payment on Duitku's hosted payment page.
6. Duitku redirects the customer to the existing RVD return destination.
7. Backend independently verifies payment through the callback and, when necessary, transaction-status reconciliation.
8. Only the backend's verified settlement process may update payment status and allocate votes.

The customer must not need to click an additional "Buka pembayaran Duitku" button or a manual "Periksa status" button.

Do not create another intermediate page unless an existing technical constraint makes direct navigation impossible. If such a constraint exists, identify it and explain why before changing the architecture.

## 3. Use the Correct Existing Payment URL

The `payment_url` returned and persisted by the existing Duitku inquiry flow is the source of truth for redirecting the customer.

Requirements:

- Trace the actual inquiry response and the code that persists `paymentUrl`.
- Confirm which URL is returned for QRIS transactions in the existing environment.
- Use the exact stored URL associated with the current payment.
- Do not reconstruct the URL from `reference`, `merchantOrderId`, or another field.
- Do not hardcode a sandbox URL, production URL, reference, or transaction ID.
- Do not substitute a URL copied from a different transaction.
- Do not assume the QRIS-specific hosted page and `topupdirectv2.aspx` are interchangeable.
- Do not change the Duitku inquiry request format or payment-method selection unless a verified defect requires it.
- If the existing record already has a valid payment URL, reuse it according to the current payment lifecycle and idempotency rules.

If `payment_url` is missing:

1. Follow the existing error-handling and retry behavior.
2. Determine whether the inquiry outcome is definitive or uncertain.
3. For an uncertain timeout, reconcile using the same merchant order ID before attempting any replacement transaction.
4. Do not create a new payment merely because the URL was not returned.
5. Do not redirect to a guessed or manually assembled URL.

Validate redirect destinations using a narrow, explicit policy based on the actual official Duitku payment hosts confirmed by the existing integration or provider documentation. Validate parsed HTTPS URLs and hostnames correctly. Do not use an unsafe substring check such as accepting any hostname that merely contains `duitku.com`.

Do not reject a legitimate existing Duitku payment URL based on an invented host allowlist. If the actual QRIS payment hostname is not covered by the existing validation policy, verify it before changing the policy.

## 4. Modify the Existing "Lanjutkan pembayaran" Button

Find the actual implementation of the existing button and modify it in place.

Required behavior:

- Clicking the button once navigates directly to the current transaction's valid `payment_url`.
- Remove the redundant intermediate click and the customer-facing manual "Periksa status" action.
- Prevent double-clicks from accidentally initiating duplicate payment creation requests.
- Preserve the existing loading, error, and retry conventions.
- Preserve current checkout and payment creation behavior.
- Do not add a new payment endpoint if the existing endpoint already provides the required payment URL.
- Do not move payment-signature generation, provider credentials, or payment verification logic into the frontend.
- Do not treat a frontend redirect as payment confirmation.

Use the actual frontend framework and established navigation conventions in the repository. Do not invent a framework-specific implementation before inspecting the code.

If the frontend receives a response that does not contain a valid payment URL, follow the existing error-handling conventions and prevent navigation. Do not silently generate a replacement URL.

## 5. Keep Internal Payment Status Backend-Only

The following are internal payment-processing states, not customer-facing interface states:

- `PENDING`
- `SUCCESS`
- `FAILED`
- `CANCELED`
- `EXPIRED`
- Any provider-specific raw status codes or messages

Requirements:

- Do not display raw internal status values or Duitku status codes to customers.
- Remove the manual status-check control from the payment UI.
- Do not add frontend polling or a new customer-facing status-check endpoint.
- Do not let the browser change internal payment status.
- Do not use a frontend timer, redirect query parameter, or browser event as evidence of payment.
- Do not expose provider error payloads, signatures, or internal exception details in the UI.

Inspect existing API response contracts before changing them. Avoid breaking unrelated frontend functionality. If an existing API response exposes internal status values, document the actual dependency and make the smallest safe change needed to prevent customer-facing status disclosure.

The frontend may navigate to the existing RVD return destination after payment. It must not decide whether payment succeeded.

Do not add a new success page, pending page, failure page, polling interface, or payment-status API unless an existing requirement demonstrably depends on it.

## 6. Return URL Is Not Payment Confirmation

Inspect and reuse the existing configured Duitku `returnUrl`.

Do not invent a new route or assume that a proposed URL exists. Prefer the existing event page or return destination already used by the application.

When Duitku redirects the customer back:

- Do not mark a payment successful from query parameters.
- Do not allocate votes from a browser request.
- Do not trust `resultCode`, `statusCode`, `reference`, or `merchantOrderId` supplied by the browser as proof of settlement.
- Do not duplicate the callback's responsibilities in the frontend.
- Do not require the customer to manually confirm or refresh payment status.

If the customer returns before callback processing finishes, allow normal navigation to the existing destination. The backend must continue to process the payment independently.

Do not add a new customer-facing payment-status mechanism as a workaround for delayed callbacks.

## 7. Callback Remains the Primary Confirmation Mechanism

Preserve the existing callback endpoint and the previous callback signature fix unless code inspection reveals a concrete defect.

Verify that the callback:

1. Parses the actual documented Duitku callback payload.
2. Verifies the HMAC-SHA256 signature using the correct field names and signing string.
3. Finds the existing payment by its actual merchant order ID.
4. Validates the amount and provider reference where available.
5. Applies permitted status transitions.
6. Calls the existing canonical settlement function for a verified successful payment.
7. Handles duplicate callbacks idempotently.
8. Does not allocate votes for pending, canceled, unknown, invalid, or mismatched transactions.

Verify the actual callback payload field names and semantics from the existing implementation and applicable Duitku documentation. Do not assume callback `resultCode` and transaction-status `statusCode` have identical meanings or structures.

Do not change the callback URL merely to implement direct redirects.

## 8. Transaction-Status Reconciliation

Reuse the existing `check_transaction_status()` implementation added in the prior Duitku timeout fix, if it exists in the current source.

Do not introduce a second status service or duplicate signature-generation logic without a demonstrated need.

Reconciliation must:

- Use the correct sandbox or production configuration.
- Use the same merchant order ID as the unresolved payment.
- Validate the returned order ID, amount, and reference against the stored payment where applicable.
- Keep unresolved or pending payments unresolved.
- Route verified success through the same canonical settlement function used by the callback.
- Follow existing cancellation and expiration transition rules.
- Treat timeout, provider errors, malformed responses, and transaction-not-found responses conservatively.
- Never grant votes from an unverified response.
- Use bounded retries and backoff.
- Avoid unbounded polling inside HTTP request handlers.
- Respect the provider's rate limits.

Inspect whether the existing project already has a scheduler, background worker, task queue, or reconciliation entry point. Reuse it if available.

If there is no automated reconciliation runner, do not pretend that adding a service method creates automatic reconciliation. Report the missing execution mechanism and implement only what can be supported safely by the existing architecture. Do not add new infrastructure dependencies without a documented technical necessity.

## 9. Preserve Idempotency and Vote Settlement

Use the existing canonical payment-settlement implementation. Do not create a separate vote-increment path for redirects or reconciliation.

Verify that:

- Payment status and vote allocation are atomic.
- Existing database locking and uniqueness constraints are preserved.
- Callback and reconciliation racing concurrently cannot allocate votes twice.
- Repeated clicks reuse the same active payment under the existing idempotency rules.
- Repeated successful callbacks cannot allocate duplicate votes.
- Unknown, pending, canceled, or mismatched provider statuses never grant votes.
- Browser redirects cannot change payment status or vote totals.

Do not alter vote calculations, pricing rules, event logic, team selection, or other business rules unrelated to this task.

## 10. Focused Tests

Use the existing testing framework, fixtures, and mocked HTTP patterns. Do not create live Duitku transactions in automated tests.

Add or update tests for the actual modified flow:

### Redirect behavior

- The existing "Lanjutkan pembayaran" action navigates directly to the stored payment URL.
- No intermediate payment-link click is required.
- Missing or invalid URLs do not cause unsafe redirects.
- Existing active payments reuse their valid payment URL.
- Repeated clicks do not create duplicate payment records.

### Payment confirmation

- A browser return does not settle the payment.
- Browser query parameters cannot allocate votes.
- A valid callback uses the existing settlement path.
- A duplicate callback does not allocate votes twice.
- A successful transaction-status reconciliation uses the same settlement path.
- Pending, canceled, unknown, and mismatched status responses do not allocate votes.

### Regression

- Existing checkout behavior and API contracts remain compatible.
- Existing timeout handling is not regressed.
- No secrets or raw provider exceptions are exposed to the frontend.
- No new payment endpoint is introduced unnecessarily.

Run only relevant tests where the environment permits. Report database-related failures separately if the configured PostgreSQL test environment is unavailable. Never claim a test passed unless it was actually executed.

## 11. Scope Control

Do not:

- Rebuild the payment architecture.
- Rewrite the Duitku service from scratch.
- Change payment methods or pricing rules.
- Create another payment table or duplicate payment service.
- Invent new database fields, API fields, route names, or environment variables.
- Hardcode URLs or transaction references.
- Change the callback URL without a verified routing defect.
- Add a frontend status polling system.
- Add new customer-facing status pages or buttons.
- Deploy to production.
- Modify production payment records.
- Change unrelated application code.

If any requested behavior conflicts with a real existing dependency, stop that specific change, explain the conflict, and choose the smallest compatible solution.

## 12. Final Verification and Report

After implementation, report:

1. The actual frontend component or template modified.
2. The actual backend functions involved.
3. The original flow and the resulting flow.
4. The source of the payment URL and how the redirect is validated.
5. Confirmation that the redundant manual payment-link and status-check actions have been removed from the relevant customer flow.
6. Confirmation that frontend redirects do not settle payments or allocate votes.
7. How callback and reconciliation share the canonical settlement logic.
8. Files changed and why each change was necessary.
9. Tests actually executed and their real results.
10. Any remaining blockers or unverified assumptions.

Keep the changes minimal. Do not report completion of callback, reconciliation, or idempotency behavior unless the code and tests actually establish it.