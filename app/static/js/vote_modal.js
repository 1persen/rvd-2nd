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

// Katalog gift (nama, level, syarat vote, durasi) datang dari backend:
// event.vote_effects. Ikon SVG dirender sekali oleh Jinja (<template data-gift>).
const SUPPORT_MAX = 150;
// Gift yang tampil di kategori "Sultan".
const SULTAN_CODES = ['roket', 'phoenix', 'naga'];

function giftIcon(animation) {
  const tpl = document.querySelector('template[data-gift="' + animation + '"]');
  return tpl ? tpl.innerHTML : '';
}

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
        const res = await fetch('/api/v1/payments/methods');
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

    get allEffects() { return this.event?.vote_effects || []; },
    // Kategori Sultan: paket tier tertinggi, ditampilkan terpisah.
    get sultanGifts() { return this.allEffects.filter(g => SULTAN_CODES.includes(g.code)); },
    get regularGifts() { return this.allEffects.filter(g => !SULTAN_CODES.includes(g.code)); },
    giftIcon(animation) { return giftIcon(animation); },
    get pricePerVote() { return Number(this.event?.price_per_vote || 0); },
    get total() { return this.pricePerVote * (Number(this.form.qty) || 0); },
    get messageLeft() { return SUPPORT_MAX - (this.form.message || '').length; },
    get votingOpen() {
      if (!this.event?.is_voting_open) return false;
      const now = Date.now();
      return (!this.event.opens_at || now >= Date.parse(this.event.opens_at)) &&
        (!this.event.closes_at || now <= Date.parse(this.event.closes_at));
    },

    // Gift = paket bundling. Klik = beli paket itu sekaligus memilih bonus efek.
    toggleEffect(effect) {
      if (this.form.effect === effect.code) {
        this.form.effect = '';
        return;
      }
      this.form.effect = effect.code;
      this.setQty(effect.votes);
    },
    // Jika qty diubah manual, pilihan gift dilepas agar tidak ada paket yang tidak cocok.
    enforceEffectRule() {
      const current = this.allEffects.find(e => e.code === this.form.effect);
      if (current && Number(this.form.qty) !== current.votes) this.form.effect = '';
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
      // Invoice yang sudah punya tautan bayar tidak boleh diganti diam-diam.
      if (this.isLoading || this.payment?.payment_url) return;
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
          // Tidak ada tautan bayar: jangan kunci pelanggan pada invoice ini.
          this.payment = null;
          this.idempotencyKey = '';
          this.error = 'Pembayaran gagal dibuat. Coba lagi atau pilih metode lain: QRIS, ShopeePay, atau DANA.';
          return;
        }
        // Satu klik: invoice berhasil dibuat langsung diarahkan ke halaman Duitku.
        this.goToPayment();
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
function supportFeed(eventId, catalog, eventName) {
  return {
    queue: [],
    seen: new Set(),
    effectQueue: [],
    currentEffect: null,
    effectTimer: null,
    tickerRaf: null,
    offset: 0,
    catalog: catalog || [],
    eventName: eventName || '',
    source: null,
    init() {
      if (!eventId) return;
      this.startTicker();
      this.trackFooter();
      // Isi awal: dukungan terbaru yang sudah tersimpan (urut lama -> baru).
      fetch('/api/v1/events/' + encodeURIComponent(eventId) + '/support-feed')
        .then(res => res.json())
        .then(body => {
          if (!body.success || !Array.isArray(body.data)) return;
          body.data.slice().reverse().forEach(msg => this.push(msg, { silent: true }));
        })
        .catch(() => {});
      if (!('EventSource' in window)) return;
      this.source = new EventSource('/api/v1/leaderboard/stream?event_id=' + encodeURIComponent(eventId));
      this.source.onmessage = (e) => {
        let msg;
        try { msg = JSON.parse(e.data); } catch (_) { return; }
        if (msg.type === 'support') this.push(msg);
      };
    },
    push(msg, opts = {}) {
      if (!msg.payment_id || this.seen.has(msg.payment_id)) return;
      this.seen.add(msg.payment_id);
      if (this.seen.size > 500) this.seen.delete(this.seen.values().next().value);
      // Hanya data publik: nama, jumlah vote, tim, dan pesan. Tidak ada kontak.
      const name = msg.supporter_name || 'Pendukung';
      const votes = Number(msg.votes || 0).toLocaleString('id-ID');
      // Format: nama · jumlah vote · tim yang divote · pesan untuk tim.
      const parts = [name, votes + ' vote'];
      if (msg.team_name) parts.push('untuk ' + msg.team_name);
      if (msg.support_message) parts.push(msg.support_message);
      this.queue.push({ id: msg.payment_id, text: parts.join(' · ') });
      if (this.queue.length > 50) this.queue.shift();
      // Riwayat awal hanya mengisi ticker; animasi hanya untuk dukungan baru.
      if (msg.effect_code && !opts.silent) this.enqueueEffect(msg.effect_code, msg.payment_id);
    },
    enqueueEffect(code, id) {
      const gift = this.catalog.find(g => g.code === code);
      if (!gift) return; // kode tidak dikenal: tetap tampil di ticker, tanpa animasi
      this.effectQueue.push({ ...gift, id });
      if (this.effectQueue.length > 10) this.effectQueue.shift();
      if (!this.currentEffect) this.nextEffect();
    },
    nextEffect() {
      clearTimeout(this.effectTimer);
      this.currentEffect = this.effectQueue.shift() || null;
      if (!this.currentEffect) return;
      const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      // Satu gift berjalan penuh sebelum gift berikutnya; durasi dari katalog backend.
      this.effectTimer = setTimeout(() => this.nextEffect(), reduce ? 1200 : this.currentEffect.duration_ms);
    },
    get tickerItems() { return this.queue; },
    // Desktop: ticker menempel di dasar konten, berhenti tepat di atas footer.
    trackFooter() {
      if (!window.matchMedia || !window.matchMedia('(min-width: 701px)').matches) return;
      const footer = document.querySelector('.public-footer');
      const stage = this.$el || document.querySelector('.support-stage');
      if (!footer || !stage) return;
      const sync = () => {
        const gap = window.innerHeight - footer.getBoundingClientRect().top;
        // 0 = menempel di dasar layar; >0 = footer mulai tampil, ikut naik.
        stage.style.bottom = (gap > 0 ? gap : 0) + 'px';
      };
      sync();
      window.addEventListener('scroll', sync, { passive: true });
      window.addEventListener('resize', sync);
    },
    // Ticker digeser lewat requestAnimationFrame (CSS animasi bisa dimatikan
    // oleh preferensi reduce-motion, sehingga teks terlihat diam).
    startTicker() {
      if (this.tickerRaf) return;
      let last = 0;
      const step = (now) => {
        const el = this.$refs?.track;
        if (el) {
          if (!last) last = now;
          this.offset -= (now - last) * 0.05; // ~50 px/detik
          const half = el.scrollWidth / 2;
          if (half > 0 && this.offset <= -half) this.offset += half;
          el.style.transform = 'translateX(' + this.offset + 'px)';
        }
        last = now;
        this.tickerRaf = requestAnimationFrame(step);
      };
      this.tickerRaf = requestAnimationFrame(step);
    },
    giftIcon(animation) {
      const tpl = document.querySelector('template[data-gift="' + animation + '"]');
      return tpl ? tpl.innerHTML : '';
    },
  };
}
