function eventsPage(event, allEvents) {
  return {
    event: event || null,
    allEvents: allEvents || [],
    tab: 'beranda',
    eventQuery: '', openOnly: false, participantQuery: '',
    dataPeserta: [],
    dataKlasemen: [],
    dataSultan: [],
    participantPage: 1,
    participantsPerPage: 12,
    loadedTabs: {},
    loading: { beranda: false, peserta: false, klasemen: false, sultan: false },
    error: { beranda: null, peserta: null, klasemen: null, sultan: null },
    isTransitioning: false,
    now: Date.now(),
    countdownTimer: null,
    qrUrl: '',
    qrOpen: false,

    init() {
      // SSR /invite/{slug} sudah mengirim event; /events?event_id= dicari di klien.
      if (!this.event) {
        const params = new URLSearchParams(window.location.search);
        const eventId = params.get('event_id');
        if (eventId) {
          this.event = this.allEvents.find(e => e.id == eventId) || null;
        }
      }

      const hash = (window.location.hash || '').replace('#', '');
      if (['beranda', 'peserta', 'klasemen', 'sultan'].includes(hash)) {
        this.tab = hash;
      }

      window.addEventListener('popstate', () => {
        const params = new URLSearchParams(window.location.search);
        const eventId = params.get('event_id');
        if (eventId) {
          const found = this.allEvents.find(e => e.id == eventId);
          if (found) this.switchEvent(found, false);
          return;
        }
        if (!window.location.pathname.startsWith('/invite/')) this.event = null;
      });

      this.$watch('tab', (value) => {
        this.fetchIfNeeded(value);
      });

      if (this.event) {
        this.fetchIfNeeded(this.tab);
        this.startCountdown();
        this.generateQrUrl();
      }
    },

    destroy() {
      if (this.countdownTimer) clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    },

    startCountdown() {
      if (this.countdownTimer) clearInterval(this.countdownTimer);
      this.countdownTimer = null;
      if (!this.event || !this.event.opens_at) return;
      this.now = Date.now();
      this.countdownTimer = setInterval(() => {
        this.now = Date.now();
      }, 1000);
    },

    get countdown() {
      if (!this.event || !this.event.opens_at) return null;
      const diff = new Date(this.event.opens_at).getTime() - this.now;
      if (!Number.isFinite(diff) || diff <= 0) return null;
      const total = Math.floor(diff / 1000);
      return {
        days: Math.floor(total / 86400),
        hours: Math.floor((total % 86400) / 3600),
        minutes: Math.floor((total % 3600) / 60),
        seconds: total % 60,
      };
    },

    setTab(tabName) {
      this.tab = tabName;
      // Pathname event tidak berubah; hash hanya penanda tab.
      if (window.history && window.history.replaceState) {
        window.history.replaceState(
          null,
          '',
          window.location.pathname + window.location.search + '#' + tabName
        );
      }
    },

    switchEvent(ev, updateUrl = true) {
      if (!ev || (this.event && this.event.id === ev.id)) return;

      this.isTransitioning = true;

      setTimeout(() => {
        this.event = ev;

        if (updateUrl) {
          history.pushState({}, '', ev.invite_path || `/events?event_id=${ev.id}`);
        }

        this.tab = 'beranda';
        this.loadedTabs = {};
        this.dataPeserta = [];
        this.dataKlasemen = [];
        this.dataSultan = [];
        this.error = { peserta: null, klasemen: null, sultan: null };
        this.participantPage = 1;
        this.participantQuery = '';

        this.startCountdown();
        this.generateQrUrl();

        this.isTransitioning = false;
      }, 300);
    },

    generateQrUrl() {
      if (!this.event) return;
      const invitePath = this.event.invite_path || window.location.pathname;
      const fullUrl = window.location.origin + invitePath;
      this.qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(fullUrl)}`;
    },

    async fetchIfNeeded(tabName) {
      if (!this.event || tabName === 'beranda' || this.loadedTabs[tabName]) return;

      const endpoints = {
        peserta: `/api/v1/events/${this.event.id}/teams`,
        klasemen: `/api/v1/leaderboard?event_id=${this.event.id}`,
        sultan: `/api/v1/events/${this.event.id}/donors`
      };

      const dataMap = {
        peserta: 'dataPeserta',
        klasemen: 'dataKlasemen',
        sultan: 'dataSultan'
      };

       try {
         this.loading[tabName] = true;
         this.error[tabName] = null;

         const res = await fetch(endpoints[tabName]);
         const result = await res.json();
         if (!res.ok || !result.success) throw new Error(result.message || 'Gagal memuat data');

         // Handle different API response shapes:
         // - teams: { data: [...array...] }
         // - leaderboard/donatur: { data: { entries: [...array...] } }
         if (Array.isArray(result.data)) {
           this[dataMap[tabName]] = result.data;
         } else if (result.data && Array.isArray(result.data.entries)) {
           this[dataMap[tabName]] = result.data.entries;
         } else {
           this[dataMap[tabName]] = [];
         }
          this.loadedTabs[tabName] = true;

          if (tabName === 'peserta') {
            const maxPage = Math.ceil(
              this.filteredParticipants.length / this.participantsPerPage
            );

            if (maxPage === 0) {
              this.participantPage = 1;
            } else if (this.participantPage > maxPage) {
              this.participantPage = maxPage;
            }
          }
        } catch(e) {
         this.error[tabName] = 'Gagal memuat data';
         console.error(`Failed to load ${tabName}:`, e);
       } finally {
         this.loading[tabName] = false;
       }
    },

    initials(name) {
      return (name || '?').trim().split(/\s+/).slice(0, 2).map(word => word[0]).join('').toUpperCase();
    },

    formatDate(value) {
      if (!value) return '';
      return new Intl.DateTimeFormat('id-ID', { day: 'numeric', month: 'short', year: 'numeric' }).format(new Date(value));
    },

    get filteredEvents() {
      const query = this.eventQuery.trim().toLocaleLowerCase('id-ID');
      return this.allEvents.filter(event => (!this.openOnly || event.is_voting_open) &&
        `${event.name} ${event.description || ''}`.toLocaleLowerCase('id-ID').includes(query));
    },

    get filteredParticipants() {
      const query = this.participantQuery.trim().toLocaleLowerCase('id-ID');
      return this.dataPeserta.filter(team => `${team.name} ${team.school || ''}`.toLocaleLowerCase('id-ID').includes(query));
    },

    get currentData() {
      return {
        peserta: this.dataPeserta,
        klasemen: this.dataKlasemen,
        sultan: this.dataSultan
      }[this.tab];
    },

    get podiumData() {
      return (this.currentData || []).slice(0, 3);
    },

    get rankingData() {
      return (this.currentData || []).slice(3);
    },

    activeClass(tabName) {
      if (this.tab === tabName) {
        return 'bg-white text-blue-600 font-semibold shadow-md';
      }
      return 'text-slate-500 hover:text-slate-700';
    },

    formatCurrency(amount) {
      return new Intl.NumberFormat('id-ID', {
        style: 'currency',
        currency: 'IDR',
        minimumFractionDigits: 0,
      }).format(amount || 0);
    },

    get paginatedParticipants() {
      const start =
          (this.participantPage - 1) * this.participantsPerPage;

      return this.filteredParticipants.slice(
          start,
          start + this.participantsPerPage
      );
    },

    get participantDisplayStart() {
      if (!this.filteredParticipants.length) return 0;

      return (this.participantPage - 1) * this.participantsPerPage + 1;
    },

    get participantDisplayEnd() {
      return Math.min(
          this.participantPage * this.participantsPerPage,
          this.filteredParticipants.length
      );
    },

    get participantTotalPages() {
      return Math.ceil(
          this.filteredParticipants.length / this.participantsPerPage
      );
    },

    nextParticipantPage() {
      if (this.participantPage < this.participantTotalPages) {
        this.participantPage++;
      }
    },

    prevParticipantPage() {
      if (this.participantPage > 1) {
        this.participantPage--;
      }
    },

    goToParticipantPage(page) {
      if (
          page >= 1 &&
          page <= this.participantTotalPages
      ) {
        this.participantPage = page;
      }
    },
  };
}
