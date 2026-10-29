/*
 * Modal voting pengganti vote.html.
 *
 * Dibuka dengan: window.dispatchEvent(new CustomEvent('vote:open', { detail: { event, team } }))
 * atau dari tautan deep link /events/{id}/vote/{team}.
 *
 * Harga, syarat efek, dan sensor pesan hanya dihitung di backend. Nilai di sini
 * hanya estimasi untuk tampilan. Animasi dan ticker hanya berasal dari frame
 * SSE type "support" yang dikirim backend setelah pembayaran terkonfirmasi.
 */
// Dipakai tombol Vote di mana pun: buka modal tanpa pindah halaman.
function openVoteModal(team, event) {
  if (!team || !event) return;
  if (!event.is_voting_open) return;
  window.dispatchEvent(new CustomEvent('vote:open', { detail: { event, team } }));
}

// Hanya ikon yang ada di frontend; label dan syarat poin datang dari backend
// (event.vote_effects) agar tidak pernah berbeda dari validasi server.
const EFFECT_ICONS = {
  rocket: '🚀', kelinci: '🐰', singa: '🐯', phoenix: '🔥', paus: '🐳', dragon: '🐉',
  kencana: '👑', pesiar: '🛥️', jet: '✈️',
};
const LUXURY_CODES = ['kencana', 'pesiar', 'jet'];
const SUPPORT_MAX = 150;

function voteModal() {
  return {
    open: false,
    event: null,
    team: null,
    form: { name: '', message: '', email: '', phone: '', qty: 1, effect: '', method: '' },
    methods: [],
    methodsLoaded: false,
    payment: null,
    error: '',
    isLoading: false,
    isNavigating: false,
    idempotencyKey: '',
    statusText: '',
    statusCheckedFor: '',
    lastFocus: null,

    init() {
      window.addEventListener('vote:open', (e) => this.show(e.detail || {}));
      window.addEventListener('keydown', (e) => { if (e.key === 'Escape' && this.open) this.close(); });
      this.$watch('form.method', () => { this.idempotencyKey = ''; this.payment = null; });
      // Setiap perubahan isi pesanan adalah invoice berbeda: kunci idempotensi
      // harus dibuat ulang agar retry tidak mengembalikan invoice lama.
      for (const field of ['qty', 'effect', 'message', 'name', 'phone']) {
        this.$watch('form.' + field, () => { if (!this.payment) this.idempotencyKey = ''; });
      }
      this.$watch('form.qty', () => this.enforceEffectRule());
      const params = new URLSearchParams(window.location.search);
      if (params.get('payment')) this.checkReturn(params.get('payment'));
    },

    show({ event, team }) {
      if (!event || !team) return;
      const changedTarget = this.event?.id !== event.id || this.team?.id !== team.id;
      this.lastFocus = document.activeElement;
      this.event = event;
      this.team = team;
      this.error = '';
      this.payment = null;
      // Invoice lama bukan invoice untuk peserta ini: jangan pernah replay kuncinya.
      if (changedTarget) this.idempotencyKey = '';
      this.open = true;
      document.body.style.overflow = 'hidden';
      this.loadMethods();
      this.$nextTick(() => document.getElementById('vote-modal-name')?.focus());
    },

    close() {
      if (this.isLoading) return;
      this.open = false;
      document.body.style.overflow = '';
      if (this.lastFocus && this.lastFocus.focus) this.lastFocus.focus();
    },

    async loadMethods() {
      if (this.methodsLoaded) return;
      try {
        const res = await fetch('/api/v1/payments/methods?amount=' + encodeURIComponent(this.event.price_per_vote || 10000));
        const body = await res.json();
        this.methods = res.ok && body.success ? body.data : [];
        if (this.methods.length && !this.methods.some(m => m.code === this.form.method)) {
          this.form.method = this.methods[0].code;
        }
      } catch (_) {
        this.methods = [];
      } finally {
        this.methodsLoaded = true;
      }
    },

    get allEffects() {
      return (this.event?.vote_effects || []).map(e => ({ ...e, icon: EFFECT_ICONS[e.code] || '✦' }));
    },
    get effects() { return this.allEffects.filter(e => !LUXURY_CODES.includes(e.code)); },
    get luxury() { return this.allEffects.filter(e => LUXURY_CODES.includes(e.code)); },
    get effectHint() {
      const first = this.effects[0];
      return first ? 'Pilih minimal ' + first.min_points + ' point untuk efek ' + first.label + '.' : '';
    },
    get pricePerVote() { return Number(this.event?.price_per_vote || 0); },
    get total() { return this.pricePerVote * (Number(this.form.qty) || 0); },
    get messageLeft() { return SUPPORT_MAX - (this.form.message || '').length; },
    get votingOpen() {
      if (!this.event?.is_voting_open) return false;
      const now = Date.now();
      return (!this.event.opens_at || now >= Date.parse(this.event.opens_at)) &&
        (!this.event.closes_at || now <= Date.parse(this.event.closes_at));
    },

    effectEnabled(effect) { return (Number(this.form.qty) || 0) >= effect.min_points; },
    toggleEffect(effect) {
      if (!this.effectEnabled(effect)) return;
      this.form.effect = this.form.effect === effect.code ? '' : effect.code;
    },
    enforceEffectRule() {
      const current = this.allEffects.find(e => e.code === this.form.effect);
      if (current && !this.effectEnabled(current)) this.form.effect = '';
    },

    setQty(value) {
      const n = Math.floor(Number(value));
      this.form.qty = Number.isFinite(n) ? Math.min(10000, Math.max(1, n)) : 1;
    },
    incrementQty() { this.setQty((Number(this.form.qty) || 0) + 1); },
    decrementQty() { this.setQty((Number(this.form.qty) || 1) - 1); },

    validate() {
      const f = this.form;
      if (!this.votingOpen) return 'Voting sedang tidak tersedia.';
      if (!f.name.trim()) return 'Nama pendukung wajib diisi.';
      if (f.name.trim().length > 100) return 'Nama pendukung maksimal 100 karakter.';
      if (f.message.length > SUPPORT_MAX) return 'Pesan dukungan maksimal 150 karakter.';
      if (!/^\d{9,13}$/.test(f.phone)) return 'Isi nomor WhatsApp dengan benar (9-13 angka).';
      f.email = f.email.trim();
      if (f.email.length > 50 || !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(f.email)) {
        return 'Isi alamat email yang valid (maksimal 50 karakter) untuk pembayaran.';
      }
      if (!Number.isInteger(Number(f.qty)) || f.qty < 1 || f.qty > 10000) return 'Jumlah vote harus antara 1 dan 10.000.';
      if (!f.method) return 'Pilih metode pembayaran terlebih dahulu.';
      return '';
    },

    submit() {
      if (this.isLoading || this.payment?.status === 'PENDING') return;
      this.error = this.validate();
      if (this.error) return;
      this.createInvoice();
    },

    async createInvoice() {
      this.isLoading = true;
      this.error = '';
      try {
        this.idempotencyKey ||= crypto.randomUUID();
        const f = this.form;
        const res = await fetch('/api/v1/payments', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': this.idempotencyKey },
          body: JSON.stringify({
            event_id: this.event.id, team_id: this.team.id, qty: Number(f.qty),
            supporter_name: f.name.trim(), supporter_email: f.email,
            supporter_phone: '+62' + f.phone, is_anonymous: false,
            payment_method: f.method,
            support_message: f.message.trim() || null,
            effect_code: f.effect || null,
          }),
        });
        const body = await res.json();
        if (!res.ok || !body.success) throw new Error(body.message || 'Pembayaran belum dapat dibuat. Silakan coba lagi.');
        this.payment = body.data;
        if (this.payment.status === 'SUCCESS') {
          this.statusText = 'Pembayaran sudah berhasil. Terima kasih atas dukunganmu.';
          return;
        }
        if (this.payment.status === 'PENDING' && !this.payment.payment_url) {
          this.error = 'Tautan pembayaran belum tersedia. Invoice ini sudah disimpan; coba lagi setelah satu menit. Jangan membuat pembayaran baru.';
        }
      } catch (err) {
        this.error = err.message || 'Tidak dapat menghubungi layanan pembayaran.';
      } finally {
        this.isLoading = false;
      }
    },

    goToPayment() {
      if (this.isNavigating || !this.payment?.payment_url) return;
      this.isNavigating = true;
      window.location.href = this.payment.payment_url;
    },

    // Kembali dari Duitku: tampilkan status dari backend. Tidak memicu animasi.
    async checkReturn(paymentId) {
      if (this.statusCheckedFor === paymentId) return;
      this.statusCheckedFor = paymentId;
      try {
        const res = await fetch('/api/v1/payments/' + encodeURIComponent(paymentId) + '/status');
        const body = await res.json();
        if (!res.ok || !body.success) return;
        const status = body.data.status;
        this.statusText = status === 'SUCCESS'
          ? 'Pembayaran berhasil. Dukunganmu sudah tercatat.'
          : status === 'PENDING'
            ? 'Pembayaran sedang menunggu konfirmasi. Muat ulang halaman ini setelah pembayaran selesai untuk melihat status terbaru.'
            : 'Pembayaran belum berhasil (' + status + ').';
      } catch (_) {
        this.statusText = 'Status pembayaran belum dapat dimuat. Coba muat ulang halaman.';
      }
    },
  };
}

/*
 * Ticker dan antrean efek. Satu koneksi SSE per halaman, dedup per payment_id.
 * Ticker dan efek berjalan terpisah: efek tidak menghentikan ticker.
 */
function supportFeed(eventId) {
  return {
    queue: [],
    seen: new Set(),
    effectQueue: [],
    currentEffect: null,
    source: null,
    init() {
      if (!eventId || !('EventSource' in window)) return;
      this.source = new EventSource('/api/v1/leaderboard/stream?event_id=' + encodeURIComponent(eventId));
      this.source.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type === 'support') this.push(msg);
      };
    },
    push(msg) {
      if (!msg.payment_id || this.seen.has(msg.payment_id)) return;
      this.seen.add(msg.payment_id);
      if (this.seen.size > 500) this.seen.delete(this.seen.values().next().value);
      const name = msg.is_anonymous ? 'Anonim' : (msg.supporter_name || 'Pendukung');
      this.queue.push({ id: msg.payment_id, text: name + ' — ' + (msg.support_message || 'Memberi dukungan') + ' (' + msg.votes + ' suara)' });
      if (this.queue.length > 50) this.queue.shift();
      if (msg.effect_code) this.enqueueEffect(msg.effect_code, msg.payment_id);
    },
    enqueueEffect(code, id) {
      this.effectQueue.push({ code, id });
      if (this.effectQueue.length > 10) this.effectQueue.shift();
      if (!this.currentEffect) this.nextEffect();
    },
    nextEffect() {
      this.currentEffect = this.effectQueue.shift() || null;
      if (!this.currentEffect) return;
      const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      setTimeout(() => this.nextEffect(), reduce ? 1500 : 4000);
    },
  };
}
