function votePage(team, event, createPaymentUrl, paymentStatusUrl) {
  return {
    team, event, createPaymentUrl, paymentStatusUrl,
    voter: { name: '', email: '', phone: '' },
    qty: 1, step: 1, payment: null, error: '', pollError: '',
    isLoading: false, isChecking: false,
    showQrModal: false, showSuccessModal: false, showMockSimulator: false,
    pollInterval: null,

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
      this.payment = null;
      this.step = 2;
      this.showQrModal = this.showMockSimulator = this.showSuccessModal = false;
      this.$refs.paymentDialog.showModal();
    },
    async createInvoice() {
      if (this.isLoading) return;
      this.isLoading = true;
      this.error = '';
      try {
        const res = await fetch(this.createPaymentUrl, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ event_id: this.event.id, team_id: this.team.id, qty: this.qty,
            supporter_name: this.voter.name.trim(), supporter_email: this.voter.email,
            supporter_phone: '+62' + this.voter.phone, is_anonymous: false }),
        });
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error(body.message || 'Pembayaran belum dapat dibuat. Silakan coba lagi.');
        this.payment = body.data;
        this.resumePayment();
        this.startPolling();
      } catch (error) { this.error = error.message || 'Tidak dapat menghubungi layanan pembayaran.'; }
      finally { this.isLoading = false; }
    },
    resumePayment() {
      this.step = 3;
      this.showMockSimulator = Boolean(this.payment?.is_mock);
      this.showQrModal = !this.showMockSimulator;
      this.showSuccessModal = false;
      if (!this.$refs.paymentDialog.open) this.$refs.paymentDialog.showModal();
    },
    startPolling() {
      clearInterval(this.pollInterval);
      this.pollInterval = setInterval(() => this.checkPayment(), 5000);
    },
    async checkPayment() {
      if (!this.payment || this.isChecking) return;
      this.isChecking = true;
      try {
        const res = await fetch(`${this.paymentStatusUrl}/${this.payment.id}/status`);
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error('Status pembayaran belum dapat diperiksa.');
        this.pollError = '';
        this.applyPayment(body.data);
      } catch (error) { this.pollError = 'Koneksi terputus. Status pembayaran akan diperiksa kembali.'; }
      finally { this.isChecking = false; }
    },
    applyPayment(payment) {
      this.payment = payment;
      if (payment.status === 'PENDING') return;
      clearInterval(this.pollInterval);
      this.pollInterval = null;
      this.showQrModal = this.showMockSimulator = false;
      if (payment.status === 'SUCCESS') {
        this.step = 3;
        this.showSuccessModal = true;
        if (!this.$refs.paymentDialog.open) this.$refs.paymentDialog.showModal();
      } else {
        this.closeModal();
        this.error = payment.status === 'FAILED' ? 'Pembayaran gagal. Silakan periksa data dan coba lagi.' : 'Pembayaran telah berakhir. Silakan buat pembayaran baru.';
      }
    },
    async simulate(action) {
      if (this.isLoading) return;
      this.isLoading = true;
      this.error = '';
      try {
        const res = await fetch(`${this.paymentStatusUrl}/mock/${this.payment.id}/simulate`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ action }),
        });
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error(body.message || 'Simulasi gagal.');
        this.applyPayment(body.data);
      } catch (error) { this.error = error.message; }
      finally { this.isLoading = false; }
    },
    closeModal() { this.$refs.paymentDialog.close(); },
    destroy() { clearInterval(this.pollInterval); },
  };
}
