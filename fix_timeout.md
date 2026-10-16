# Fix Duitku Timeout, Transaction Status Reconciliation, and Payment Tests

## 1. Objective

Audit and fix the existing Duitku payment integration in Raja Voting Digital (`rvd-2nd`).

The current integration can create a payment record, but an inquiry request may encounter a timeout or HTTP 504. An uncertain transaction may subsequently be marked `EXPIRED`, even though the payment provider's actual transaction status has not been confirmed.

Implement reliable transaction-status reconciliation, safe timeout handling, and automated regression tests while preserving the existing architecture, API contracts, database schema, and payment flow wherever possible.

**Do not perform a broad refactor. Inspect the existing implementation first and make the smallest correct changes.**

## 2. Verified Test Results

Manual tests against Duitku Sandbox have produced the following results.

### Test A: Create transaction

Endpoint:

`POST https://sandbox.duitku.com/webapi/api/merchant/v2/inquiry`

Request returned:

- HTTP status: `200`
- `statusCode`: `"00"`
- `statusMessage`: `"SUCCESS"`
- `paymentUrl`: populated
- `reference`: populated
- `amount`: `"10000"`

Test order ID:

`RVDTEST20261016132936`

Test reference:

`DS3660126KGCGUXLD1NMSG1L`

Amount: IDR 10,000.

### Test B: Check transaction status

Endpoint:

`POST https://sandbox.duitku.com/webapi/api/merchant/transactionStatus`

Request returned:

- HTTP status: `200`
- `merchantOrderId`: `RVDTEST20261016132936`
- `reference`: `DS3660126KGCGUXLD1NMSG1L`
- `amount`: `"10000"`
- `statusCode`: `"01"`
- `statusMessage`: `"PROCESS"`

This is expected because the test transaction has not been paid. It confirms that transaction creation and transaction-status lookup work from the developer's local environment.

### Test C: Existing RVD failure

Previously observed logs:

- RVD create-payment API returned HTTP 200.
- Request duration was approximately 15.35 seconds.
- Retry reused the same invoice/order and did not create a duplicate payment record.
- A subsequent request surfaced HTTP 500 with a message containing `504 Gateway Timeout`.
- The internal RVD payment record was reported as `EXPIRED`, with `payment_url = null`.

Do not assume the HTTP 504 originated directly from Duitku. Trace the actual outbound request, exception chain, and response handling to determine where the timeout occurs.

The manually created test transaction is not registered in the RVD database. Do not use it to modify production payment records or increment votes.

## 3. Inspect Existing Code First

Review the relevant existing implementation, including:

- `app/services/duitku_service.py`
- `app/services/payment_service.py`
- `app/routers/payments.py`
- Existing payment models and repositories
- Existing callback handlers
- Existing payment-status enums/constants
- Existing exception handlers and HTTP client configuration
- Existing payment and Duitku tests

Determine the actual call sequence from checkout initiation to Duitku inquiry, database persistence, callback processing, and vote allocation.

Reuse existing abstractions and conventions. Do not create duplicate services, endpoints, payment models, or status definitions when equivalent implementations already exist.

## 4. Fix Callback Signature Verification

Audit `verify_callback_signature()` and all references to the callback signing string.

A previously identified potential bug uses:

`merchant_orderId`

where the actual variable is:

`merchant_order_id`

Fix any undefined or incorrectly referenced variable.

For the callback signature, verify the existing implementation against Duitku's applicable API specification. The documented signing string is:

`merchantCode + amount + merchantOrderId`

The HMAC algorithm is SHA-256 using the API key, with a hexadecimal signature representation.

Requirements:

- Validate the incoming signature before processing the callback.
- Reject invalid signatures without changing payment status or vote totals.
- Keep the existing callback URL and route unless the audit proves that a route change is required.
- Preserve the existing supported callback content types.
- Add regression tests for valid signatures, invalid signatures, and malformed payloads.

## 5. Implement Duitku Transaction Status Lookup

Implement or repair a reusable transaction-status method in the existing Duitku service.

### Sandbox endpoint

`https://sandbox.duitku.com/webapi/api/merchant/transactionStatus`

### Production endpoint

`https://passport.duitku.com/webapi/api/merchant/transactionStatus`

Use the existing environment-based sandbox/production configuration. Never hardcode production credentials or silently switch environments.

### Request

HTTP method: `POST`.

Use the request encoding required by the current Duitku integration specification. The following fields are required:

- `merchantCode`
- `merchantOrderId`
- `signature`

The documented signature is:

`HMAC-SHA256(merchantCode + merchantOrderId, API_KEY)`

Use the API key as the HMAC secret and encode the digest as lowercase hexadecimal.

Do not include the payment amount in this status-lookup signature unless the applicable current Duitku specification explicitly requires it.

### Response handling

Normalize the provider response into the existing internal payment-status representation.

Documented response examples include:

- `statusCode = "00"`: successful payment
- `statusCode = "01"`: pending or processing
- `statusCode = "02"`: canceled

Do not assume every unrecognized status is a failure. Handle unknown statuses conservatively and log sufficient diagnostic context.

Validate the returned merchant order ID, reference when available, and amount against the corresponding internal payment record before applying any status transition.

A missing transaction response must not automatically be treated as proof that payment failed. It may indicate an invalid order ID, environment mismatch, propagation delay, or an actual missing transaction. Preserve the unresolved state and record a diagnostic result.

## 6. Correct Inquiry Timeout and HTTP 504 Handling

Audit the existing inquiry request, HTTP client timeout configuration, exception handling, and database transaction boundaries.

Differentiate the following conditions:

1. **Definitive provider rejection:** The provider explicitly rejects the request with a documented response that proves the transaction was not created.
2. **Transport timeout or HTTP 504:** The provider's outcome is uncertain. The request may have reached Duitku even if RVD did not receive the response.
3. **Successful inquiry:** Duitku returns a valid successful response containing the transaction reference and payment URL.
4. **Malformed or incomplete response:** The result cannot be trusted and requires safe error handling.

### Required behavior for uncertain outcomes

- Do not automatically mark an uncertain payment `EXPIRED` solely because of a timeout, network exception, HTTP 502, HTTP 503, or HTTP 504.
- Do not report the payment as successful without verified evidence.
- Preserve an existing valid payment URL and provider reference if available.
- If the existing schema supports a `PENDING` state, use it for unresolved transactions where appropriate. If the architecture has a more suitable existing state, reuse it.
- If the schema has no way to represent an uncertain outcome, assess whether an existing pending state is sufficient before proposing a migration.
- Return a response consistent with the existing API contract. Do not expose raw provider exceptions, API keys, or sensitive request payloads.
- Persist sufficient information to retry status reconciliation safely.
- Use bounded HTTP timeouts and bounded retries with backoff. Do not repeatedly submit new inquiry requests after an ambiguous timeout.
- Do not generate a new merchant order ID for a retry of the same logical payment.
- Preserve the existing idempotency behavior that reuses the same invoice/order and avoids duplicate payment records.

A timeout does not prove that Duitku failed to create the transaction. Reconciliation must precede any decision to create a replacement transaction.

## 7. Reconciliation Workflow

Integrate transaction-status lookup into the existing payment workflow with minimal architectural changes.

When an inquiry result is uncertain:

1. Keep the internal payment unresolved.
2. Reuse the existing merchant order ID.
3. Query Duitku transaction status using the correct environment and signature.
4. Validate the provider response against the internal payment record.
5. Apply only a verified, permitted state transition.
6. If Duitku reports pending/processing, keep the payment pending.
7. If Duitku reports success, invoke the existing idempotent payment-settlement logic.
8. If Duitku reports cancellation, apply the existing cancellation transition only after validating the response.
9. If the provider remains unavailable or returns an inconclusive response, retain the unresolved state and retry later within configured limits.

Do not introduce an unbounded polling loop in a request handler.

Prefer an existing background-job, scheduled-task, or reconciliation mechanism if the project already has one. If none exists, implement a narrowly scoped callable reconciliation service and document how it can be invoked by an existing scheduler. Do not add a new infrastructure dependency unless necessary.

Respect Duitku rate limits. Avoid aggressive repeated transaction-status calls.

## 8. Payment Status and EXPIRED Recovery

Audit every code path that sets a payment to `EXPIRED`, including:

- Inquiry exceptions
- HTTP errors
- Checkout retries
- Payment expiration jobs
- Callback processing
- Transaction-status reconciliation

Expiration must follow the existing business rules and must not be used as a generic representation of an unknown provider outcome.

If existing records were marked `EXPIRED` because of an uncertain timeout:

- Do not blindly reset every expired payment to pending.
- Identify the records eligible for reconciliation based on available status, timestamps, provider reference, and order ID.
- Verify their current provider status before changing internal status.
- Apply a narrowly scoped recovery path that does not duplicate votes or payment records.
- Do not overwrite a terminal status without an explicitly defined and safe transition rule.

Do not add an automatic data migration or bulk status rewrite unless the existing architecture demonstrably requires it.

## 9. Idempotent Settlement and Vote Allocation

Preserve and verify the existing payment-settlement transaction.

The same successful payment must never increment the vote total more than once, even if success is reported through both the callback and status reconciliation.

Requirements:

- Use the existing database transaction and locking strategy.
- Preserve the uniqueness guarantee on `VoteLog.payment_id`, if present.
- Validate the payment amount and reference before settlement.
- Apply the successful payment state and vote allocation atomically.
- Handle concurrent callbacks and reconciliation attempts safely.
- A repeated callback or repeated status lookup must not create additional votes.
- Failed validation must not change the team's vote count.
- Do not create a separate vote-allocation implementation for reconciliation. Reuse the canonical existing settlement path.

## 10. Automated Regression Tests

Add tests using mocked HTTP responses. Unit tests must not depend on live Duitku availability or real credentials.

Cover the following scenarios.

### Inquiry tests

- Successful inquiry returns a valid reference and payment URL.
- Inquiry timeout does not create duplicate payment records.
- HTTP 504 does not automatically mark an uncertain payment as expired.
- Retrying the same logical payment reuses the existing merchant order ID.
- Malformed or incomplete provider responses are handled safely.
- Existing successful checkout behavior remains compatible.

### Transaction-status tests

- Correct HMAC-SHA256 signature generation.
- Status `00` triggers the canonical settlement path.
- Status `01` leaves payment pending.
- Status `02` follows the existing cancellation rules.
- Transaction not found remains unresolved until sufficient evidence exists.
- HTTP timeout and provider errors do not falsely settle or expire a payment.
- Amount, order ID, or reference mismatches are rejected safely.
- Unknown provider status does not grant votes.

### Callback and idempotency tests

- Valid callback succeeds.
- Invalid signature is rejected.
- The previously identified variable-name bug does not recur.
- Duplicate successful callbacks do not increment votes twice.
- Callback and reconciliation racing concurrently still produce one settlement.
- Existing unique constraints and database locking behavior are respected.

### Regression expectations

- Preserve existing tests and API response contracts.
- Use mocks for external HTTP calls.
- Use the existing test framework and fixtures.
- Do not disable failing tests or weaken assertions merely to obtain a passing result.

## 11. Logging and Diagnostics

Add structured, useful logs around inquiry and reconciliation.

Include, where available:

- Internal payment ID
- Merchant order ID
- Provider reference
- Operation name (`inquiry`, `transaction_status`, `callback`)
- HTTP status
- Provider status code
- Elapsed time
- Retry/reconciliation attempt count
- Exception category

Never log:

- API keys
- Full signatures
- Authorization secrets
- Sensitive customer data unnecessarily
- Full unredacted payment payloads

Make it possible to distinguish a provider timeout from a local HTTP client timeout, connection failure, response parsing failure, and database persistence failure.

## 12. Configuration and Security

- Reuse the existing Duitku configuration and environment variable names.
- Keep sandbox and production URLs separate.
- Do not print or expose secrets.
- Do not change the deployed callback URL without evidence that the current route is incorrect.
- Avoid changing database schema unless strictly necessary.
- Do not install new dependencies if the existing HTTP client can handle the requirements.

## 13. Implementation Procedure

Follow this order:

1. Inspect the existing payment architecture and identify the precise failure path.
2. Fix the callback signature variable-name bug.
3. Implement or repair the transaction-status service.
4. Correct uncertain-timeout handling.
5. Integrate bounded reconciliation into the existing workflow.
6. Ensure payment settlement and vote allocation remain idempotent.
7. Add focused regression tests.
8. Run the relevant existing test suite and report actual results.
9. Review changes for API compatibility, status-transition correctness, and accidental secret exposure.

Do not claim tests passed unless they were actually executed.

## 14. Acceptance Criteria

The task is complete when:

- Duitku inquiry succeeds without breaking existing checkout behavior.
- A timeout or HTTP 504 does not automatically cause an uncertain payment to become `EXPIRED`.
- Existing order IDs are reused for ambiguous retries.
- Transaction status can be queried with the correct signature and environment.
- Pending transactions remain pending.
- Only verified successful payments trigger vote allocation.
- Duplicate callbacks and concurrent reconciliation cannot allocate votes twice.
- Callback signature verification no longer fails because of the variable-name bug.
- Regression tests cover timeout, status reconciliation, invalid signatures, and idempotent settlement.
- Existing database schema and API contracts remain unchanged unless a justified change is documented.
- The final report identifies modified files, root cause, test commands, actual test results, and any remaining limitations.

## 15. Final Report Format

After implementation, report:

1. Root cause confirmed from code or logs.
2. Files changed and the purpose of each change.
3. How inquiry timeouts and HTTP 504 are handled.
4. How transaction-status reconciliation works.
5. How duplicate settlement is prevented.
6. Tests added and commands executed.
7. Actual test results, including failures.
8. Any remaining manual sandbox tests required.

Do not stop after producing a diagnosis or a plan. Implement the fixes, subject to the existing project architecture and available test environment.