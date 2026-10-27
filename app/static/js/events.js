function eventsPage(event, allEvents, config) {
  config = config || {};
  const theme = (event && event.theme) || config.theme || {};
  return {
    event: event || null,
    allEvents: allEvents || [],
    config: config,
    theme: theme,
    preview: !!config.preview,
    catalog: !!config.catalog,
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
    now: Date.now(),
    countdownTimer: null,
    qrUrl: '',
    qrOpen: false,

    get showParticipants() {
      return this.theme.show_participants !== false;
    },

    get showCountdown() {
      return this.theme.show_countdown !== false;
    },

    get availableTabs() {
      return ['beranda', 'peserta', 'klasemen', 'sultan'].filter(
        tabName => tabName !== 'peserta' || this.showParticipants
      );
    },

    init() {
      if (this.config.collections) {
        const collections = this.config.collections;
        if (Array.isArray(collections.peserta)) {
          this.dataPeserta = collections.peserta;
          this.loadedTabs.peserta = true;
        }
        if (Array.isArray(collections.klasemen)) {
          this.dataKlasemen = collections.klasemen;
          this.loadedTabs.klasemen = true;
        }
        if (Array.isArray(collections.sultan)) {
          this.dataSultan = collections.sultan;
          this.loadedTabs.sultan = true;
        }
      }
      if (this.preview) {
        this.loadedTabs = { beranda: true, peserta: true, klasemen: true, sultan: true };
      }

      const hash = (window.location.hash || '').replace('#', '');
      if (this.availableTabs.includes(hash)) {
        this.tab = hash;
      }

      this.$watch('tab', (value) => {
        this.fetchIfNeeded(value);
      });

      if (this.event) {
        this.fetchIfNeeded(this.tab);
        this.startCountdown();
        if (!this.preview) this.generateQrUrl();
      }
    },

    destroy() {
      if (this.countdownTimer) clearInterval(this.countdownTimer);
      this.countdownTimer = null;
    },

    startCountdown() {
      if (this.countdownTimer) clearInterval(this.countdownTimer);
      this.countdownTimer = null;
      if (!this.showCountdown || !this.event || !this.event.opens_at) return;
      this.now = Date.now();
      this.countdownTimer = setInterval(() => {
        this.now = Date.now();
      }, 1000);
    },

    get countdown() {
      if (!this.showCountdown || !this.event || !this.event.opens_at) return null;
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
      if (tabName === 'peserta' && !this.showParticipants) {
        tabName = 'klasemen';
      }
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

    generateQrUrl() {
      if (!this.event) return;
      const invitePath = this.event.invite_path || window.location.pathname;
      const fullUrl = window.location.origin + invitePath;
      this.qrUrl = `https://api.qrserver.com/v1/create-qr-code/?size=200x200&data=${encodeURIComponent(fullUrl)}`;
    },

    voteHref(team) {
      if (this.preview || !this.event || !team) return null;
      return '/events/' + encodeURIComponent(this.event.id) + '/vote/' + encodeURIComponent(team.id);
    },

    async fetchIfNeeded(tabName) {
      // Preview renders server-supplied collections and must never hit a public API.
      if (this.preview || !this.event || tabName === 'beranda' || this.loadedTabs[tabName]) return;

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

    activeCategory: '',

    educationLevelLabels: {
      'sma-smk-ma': 'SMA/SMK/MA',
      'smp-mts': 'SMP/MTs',
      'sd-mi': 'SD/MI',
      'purna-klub': 'PURNA/KLUB',
    },

    categoryLabel(level) {
      return this.educationLevelLabels[level] || level;
    },

    get participantCategories() {
      const set = new Set();
      this.dataPeserta.forEach(team => { if (team.education_level) set.add(team.education_level); });
      return Array.from(set);
    },

    setCategory(cat) {
      this.activeCategory = cat;
      this.participantPage = 1;
    },

    get participantCountdown() {
      if (!this.showCountdown) return null;
      const target = this.event?.closes_at || this.event?.opens_at;
      if (!target) return null;
      const diff = new Date(target).getTime() - this.now;
      if (!Number.isFinite(diff) || diff <= 0) return null;
      const total = Math.floor(diff / 1000);
      return {
        days: Math.floor(total / 86400),
        hours: Math.floor((total % 86400) / 3600),
        minutes: Math.floor((total % 3600) / 60),
        seconds: total % 60,
      };
    },

    get filteredParticipants() {
      const query = this.participantQuery.trim().toLocaleLowerCase('id-ID');
      return this.dataPeserta.filter(team =>
        (!this.activeCategory || team.education_level === this.activeCategory) &&
        `${team.name} ${team.school || ''}`.toLocaleLowerCase('id-ID').includes(query));
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

    async downloadFile(url, filename) {
      if (!url) return;
      try {
        const response = await fetch(url, { mode: 'cors' });
        if (!response.ok) throw new Error('Fetch gagal');
        const blob = await response.blob();
        const blobUrl = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = blobUrl;
        a.download = filename || 'download';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(blobUrl);
      } catch (err) {
        // Fallback: open in new tab if fetch fails (CORS, network, etc.)
        window.open(url, '_blank');
      }
    }
  };
}