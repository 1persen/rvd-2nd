function votePage(team, event, createPaymentUrl, paymentStatusUrl) {
  return {
    team, event, createPaymentUrl, paymentStatusUrl,
    voter: { name: '', email: '', phone: '' },
    qty: 1, payment: null, error: '',
    isLoading: false, isNavigating: false, idempotencyKey: '',
    methods: [], methodsLoaded: false, paymentMethod: '',

    init() {
      // No sessionStorage restore or polling. The payment URL is navigated to
      // directly via the "Lanjutkan pembayaran" button. Payment settlement is
      // handled exclusively by the backend callback and reconciliation.
      this.loadMethods();
      this.$watch('paymentMethod', () => {
        // A different method is a different invoice: never replay the old key.
        this.idempotencyKey = '';
        this.payment = null;
      });
    },

    async loadMethods() {
      try {
        const res = await fetch('/api/v1/payments/methods?amount=' + encodeURIComponent(this.event.price_per_vote || 10000));
        const body = await res.json();
        this.methods = res.ok && body.success ? body.data : [];
        if (this.methods.length && !this.methods.some(m => m.code === this.paymentMethod)) {
          this.paymentMethod = this.methods[0].code;
        }
      } catch (_) {
        this.methods = [];
      } finally {
        this.methodsLoaded = true;
      }
    },

    get isVotingOpen() {
      if (!this.event?.is_voting_open) return false;
      const now = Date.now();
      return (!this.event.opens_at || now >= Date.parse(this.event.opens_at)) &&
        (!this.event.closes_at || now <= Date.parse(this.event.closes_at));
    },
    get totalPrice() { return this.event.price_per_vote * (Number(this.qty) || 0); },
    formatCurrency(amount) {
      return new Intl.NumberFormat('id-ID', { style: 'currency', currency: 'IDR', maximumFractionDigits: 0 }).format(amount || 0);
    },
    incrementQty() { this.qty = Math.min(10000, (Number(this.qty) || 0) + 1); },
    decrementQty() { this.qty = Math.max(1, (Number(this.qty) || 1) - 1); },
    nextStep() {
      this.error = '';
      if (!this.isVotingOpen) { this.error = 'Voting sedang tidak tersedia.'; return; }
      if (!this.voter.name.trim() || !/^\d{9,13}$/.test(this.voter.phone)) {
        this.error = 'Isi nama dan nomor WhatsApp dengan benar.'; return;
      }
      this.voter.email = this.voter.email.trim();
      if (this.voter.email.length > 50 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(this.voter.email)) {
        this.error = 'Isi alamat email yang valid (maksimal 50 karakter) untuk pembayaran.'; return;
      }
      if (!Number.isInteger(this.qty) || this.qty < 1 || this.qty > 10000) {
        this.error = 'Jumlah vote harus berupa angka bulat antara 1 dan 10.000.'; return;
      }
      if (!this.paymentMethod) {
        this.error = 'Pilih metode pembayaran terlebih dahulu.'; return;
      }
      this.payment = null;
      this.createInvoice();
    },
    async createInvoice() {
      if (this.isLoading) return;
      this.isLoading = true;
      this.error = '';
      try {
        this.idempotencyKey ||= crypto.randomUUID();
        const res = await fetch(this.createPaymentUrl, {
          method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': this.idempotencyKey },
          body: JSON.stringify({ event_id: this.event.id, team_id: this.team.id, qty: this.qty,
            supporter_name: this.voter.name.trim(), supporter_email: this.voter.email,
            supporter_phone: '+62' + this.voter.phone, is_anonymous: false,
            payment_method: this.paymentMethod }),
        });
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error(body.message || 'Pembayaran belum dapat dibuat. Silakan coba lagi.');
        this.payment = body.data;
        if (this.payment.status === 'PENDING' && this.payment.payment_url) {
          // Payment URL is available; customer navigates via "Lanjutkan pembayaran".
          return;
        }
        if (this.payment.status === 'SUCCESS') {
          this.error = 'Pembayaran sudah berhasil.';
          return;
        }
        // PENDING without payment_url: uncertain outcome, do not navigate.
        this.error = 'Tautan pembayaran belum tersedia. Invoice ini sudah disimpan; coba lagi setelah satu menit.';
      } catch (error) { this.error = error.message || 'Tidak dapat menghubungi layanan pembayaran.'; }
      finally { this.isLoading = false; }
    },
    goToPayment() {
      if (this.isNavigating || !this.payment?.payment_url) return;
      this.isNavigating = true;
      window.location.href = this.payment.payment_url;
    },
  };
}
