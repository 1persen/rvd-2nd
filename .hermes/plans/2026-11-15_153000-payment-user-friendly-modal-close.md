# Plan: Payment User-Friendly Flow After Accidental Modal Close

## Goal
When a user accidentally closes the payment modal after submitting a vote, reopening the modal should show a "Lanjutkan Pembayaran" button (if `payment_url` exists) or an obvious "Periksa Status Pesanan" action — never a locked form with no clear next step.

## Current Context / Assumptions

**Repo**: `C:/Users/Indra/Downloads/rvd-2nd`
- Frontend: Alpine.js modal in `app/static/js/vote_modal.js` + template `app/templates/partials/vote_modal.html`
- Backend: FastAPI payment endpoints in `app/api/payments.py`, service in `app/services/payment_service.py`
- Duitku integration: inquiry returns `unknown` (phase 1) → no `payment_url`; later success (phase 2) → `payment_url` available
- Storage: `sessionStorage` key `rvd.checkout.{event_id}.{team_id}` (TTL 24h)
- Current behavior: phase 1 → locked form, only "Periksa Status Pesanan"; phase 2 → "Lanjutkan Pembayaran" appears

**Problem**: Users don't understand "Periksa Status Pesanan" = check if payment URL is ready. They expect to continue payment directly.

## Architecture / Proposed Approach

1. **Always show a primary action button** in the status panel: "Lanjutkan Pembayaran" (if `payment_url` exists) OR "Periksa Status Pesanan" (if not) — never both hidden.
2. **Auto-check status on modal reopen** (optional, configurable) so `payment_url` appears without manual click.
3. **Clearer UI copy**: "Menunggu tautan pembayaran..." + "Periksa Sekarang" instead of ambiguous "Periksa Status Pesanan".

Changes confined to:
- `app/static/js/vote_modal.js` — `restoreOrder()`, `show()`, `reconcileOrder()`, template rendering logic
- `app/templates/partials/vote_modal.html` — status panel buttons
- (Optional) `app/services/payment_service.py` — ensure `/reconcile` returns `payment_url` when Duitku has it

## Step-by-Step Tasks

### Task 1: Update Template — Always Show Primary Action
**File**: `app/templates/partials/vote_modal.html` (lines 36-49)

**Current** (lines 36-45):
```html
<div class="vote-order-actions">
  <button type="button" class="btn btn-primary vote-modal-full"
          x-show="order?.status === 'PENDING' && order?.payment_url"
          @click="goToPayment()" :disabled="isNavigating">Lanjutkan Pembayaran</button>
  <button type="button" class="btn btn-secondary vote-modal-full"
          x-show="order?.status === 'PENDING' && !order?.needs_manual_review"
          @click="reconcileOrder()" :disabled="isChecking">
    <span x-show="!isChecking">Periksa Status Pesanan</span>
    <span x-show="isChecking" x-cloak>Memeriksa status…</span>
  </button>
  ...
</div>
```

**New** — single primary button that adapts:
```html
<div class="vote-order-actions">
  <!-- Primary action: always visible for PENDING orders -->
  <template x-if="order?.status === 'PENDING'">
    <button type="button" class="btn btn-primary vote-modal-full"
            x-show="order.payment_url"
            @click="goToPayment()" :disabled="isNavigating">
      Lanjutkan Pembayaran
    </button>
    <button type="button" class="btn btn-primary vote-modal-full"
            x-show="!order.payment_url && !order.needs_manual_review"
            @click="reconcileOrder()" :disabled="isChecking">
      <span x-show="!isChecking">Periksa Tautan Pembayaran</span>
      <span x-show="isChecking" x-cloak>Memeriksa…</span>
    </button>
    <button type="button" class="btn btn-secondary vote-modal-full"
            x-show="order.needs_manual_review"
            @click="reconcileOrder()" :disabled="isChecking">
      <span x-show="!isChecking">Periksa Status (Manual Review)</span>
      <span x-show="isChecking" x-cloak>Memeriksa…</span>
    </button>
  </template>
  <button type="button" class="btn btn-secondary vote-modal-full"
          x-show="reconcileVerdict === 'ABSENT_CONFIRMED'"
          @click="startNewInvoice()">Coba Buat Tagihan Baru</button>
  <a class="btn btn-secondary vote-modal-full" :href="helpUrl" target="_blank" rel="noopener">Hubungi Bantuan</a>
</div>
```

**Verification**: Open modal after submit → primary button always visible (blue for "Lanjutkan"/"Periksa", gray for manual review).

---

### Task 2: Auto-Reconcile on Modal Reopen (Optional, Configurable)
**File**: `app/static/js/vote_modal.js` — `show()` method (around line 93)

**Current**:
```javascript
show({ event, team }) {
  ...
  if (!changedTarget) this.restoreOrder();
  this.open = true;
  ...
  // JANGAN panggil checkStatus() di sini — status hanya muncul setelah
  // user klik "Periksa Status Pesanan" (reconcileOrder).
}
```

**New** — add auto-reconcile for orders without `payment_url`:
```javascript
show({ event, team }) {
  ...
  if (!changedTarget) this.restoreOrder();
  this.open = true;
  ...
  
  // Auto-reconcile if we have an order but no payment_url yet
  // This fetches the latest status from backend (may now have payment_url)
  if (!changedTarget && this.order?.status === 'PENDING' && !this.order.payment_url && !this.order.needs_manual_review) {
    // Small delay to let UI render first
    this.$nextTick(() => {
      setTimeout(() => this.reconcileOrder(), 500);
    });
  }
}
```

**Verification**: Close modal → reopen → "Periksa Tautan Pembayaran" button briefly shows "Memeriksa…" → if backend now has `payment_url`, button becomes "Lanjutkan Pembayaran".

---

### Task 3: Improve Status Panel Copy
**File**: `app/templates/partials/vote_modal.html` (lines 22-35)

**Current**:
```html
<div class="vote-order" x-show="order" x-cloak role="status">
  <div x-show="hasCheckedStatus" x-cloak>
    <p class="vote-order-kicker">Status pembayaran</p>
    <h3 class="vote-order-title" x-text="orderLabel"></h3>
  </div>
  ...
  <p class="vote-order-hint" x-show="order?.status === 'PENDING'">
    Jangan melakukan pembayaran melalui tagihan lain.
  </p>
```

**New** — show helpful hint when `payment_url` is missing:
```html
<div class="vote-order" x-show="order" x-cloak role="status">
  <div x-show="hasCheckedStatus || order.payment_url" x-cloak>
    <p class="vote-order-kicker">Status pembayaran</p>
    <h3 class="vote-order-title" x-text="orderLabel"></h3>
  </div>
  <dl class="vote-order-meta">
    <div><dt>Nama</dt><dd x-text="order?.supporter_name"></dd></div>
    <div><dt>Nomor HP</dt><dd x-text="order?.supporter_phone"></dd></div>
    <div><dt>Pesanan</dt><dd x-text="(order?.votes || 0) + ' vote'"></dd></div>
    <div><dt>ID Transaksi</dt><dd x-text="order?.id"></dd></div>
  </dl>
  <template x-if="order?.status === 'PENDING'">
    <p class="vote-order-hint" x-show="order.payment_url">
      Tautan pembayaran siap. Klik "Lanjutkan Pembayaran" untuk bayar via Duitku.
    </p>
    <p class="vote-order-hint" x-show="!order.payment_url && !order.needs_manual_review">
      Menunggu tautan pembayaran dari penyedia. Klik "Periksa Tautan Pembayaran" untuk cek ulang.
    </p>
    <p class="vote-order-hint" x-show="order.needs_manual_review">
      Pesanan dalam peninjauan manual. Klik "Periksa Status (Manual Review)" untuk cek ulang.
    </p>
  </template>
</div>
```

**Verification**: Status panel shows context-aware hint explaining what the button does.

---

### Task 4: Ensure Backend Returns `payment_url` on Reconcile
**File**: `app/services/payment_service.py` — `reconcile_payment()` method

**Check**: Verify `/api/v1/payments/{id}/reconcile` returns `payment_url` when Duitku inquiry succeeds.

**Expected response shape** (already in `orderFrom`):
```python
{
  "id": "...",
  "status": "PENDING",
  "payment_url": "https://sandbox.duitku.com/...",  # or null
  "inquiry_state": "SUCCESS",
  "needs_manual_review": false,
  ...
}
```

**Verification**: Call `/reconcile` manually → confirm `payment_url` present when Duitku has it.

---

### Task 5: E2E Test — Accidental Close Flow
**File**: Create `tests/browser/test_modal_close_reopen.py` (or extend existing browser smoke test)

**Scenario**:
1. Open event page
2. Click "VOTE SEKARANG" → fill form → submit
3. Wait for invoice created (status PENDING, no payment_url)
4. Close modal (click "Tutup")
5. Click "VOTE SEKARANG" again for same team
6. **Assert**: Status panel visible, primary button says "Periksa Tautan Pembayaran" (blue)
7. Click button → wait for reconcile
8. **Assert**: If backend returns `payment_url` → button becomes "Lanjutkan Pembayaran"
9. Click "Lanjutkan Pembayaran" → verify redirect to Duitku URL

**Command to run**:
```bash
cd /c/Users/Indra/Downloads/rvd-2nd
pytest tests/browser/test_modal_close_reopen.py -v
```

---

## Tests / Validation

| Task | Test Command | Expected Output |
|------|--------------|-----------------|
| 1 Template | Manual: open modal after submit | Primary button always visible (blue) |
| 2 Auto-reconcile | Manual: close → reopen modal | Button shows "Memeriksa…" then updates |
| 3 Copy | Manual: inspect status panel | Context-aware hint text visible |
| 4 Backend | `curl -X POST /api/v1/payments/{id}/reconcile` | `payment_url` in response when ready |
| 5 E2E | `pytest tests/browser/test_modal_close_reopen.py -v` | All assertions pass |

---

## Risks, Tradeoffs, Open Questions

| Risk | Mitigation |
|------|------------|
| Auto-reconcile on reopen may spam backend if user opens/closes rapidly | Debounce: only auto-reconcile once per modal open; `hasCheckedStatus` guard |
| `payment_url` may expire (Duitku expires_at) | `restoreOrder()` already checks `expires_at` → calls `forgetOrder()` if expired |
| User expects instant redirect without clicking button | Keep explicit click — redirect on load is confusing & breaks back button |
| Manual review orders need different copy | Template already handles `needs_manual_review` branch |

**Open Questions**:
1. Should auto-reconcile be enabled by default, or behind a feature flag? (Default: ON — better UX)
2. Max auto-reconcile retries? (Current polling stops at 75×8s = 10min; reconcile is manual-triggered)
3. Should we also poll `/status` on modal open? (Already done via `startPolling()` in `restoreOrder()`)

---

## Files to Modify

1. `app/templates/partials/vote_modal.html` — Tasks 1, 3
2. `app/static/js/vote_modal.js` — Task 2 (show method)
3. `app/services/payment_service.py` — Task 4 (verify only)
4. `tests/browser/test_modal_close_reopen.py` — Task 5 (new file)

---

## Commit Plan

```bash
# Task 1 + 3: Template updates
git add app/templates/partials/vote_modal.html
git commit -m "feat(payment): always show primary action in status panel; improve copy"

# Task 2: Auto-reconcile on reopen
git add app/static/js/vote_modal.js
git commit -m "feat(payment): auto-reconcile on modal reopen when payment_url missing"

# Task 5: E2E test
git add tests/browser/test_modal_close_reopen.py
git commit -m "test(browser): add modal close/reopen payment flow test"
```