/**
 * Event detail page Alpine component, manages event list vs detail view
 * and the three internal tabs: Peserta, Leaderboard Tim, Donatur.
 *
 * @param {Array} initialEvents - all events with stats
 * @param {object|null} initialEvent - selected event or null
 * @param {Array} initialTeams - teams for the selected event
 * @param {object} initialLeaderboard - leaderboard payload with entries
 * @param {object} initialDonors - donors payload with entries
 */
function eventPage(initialEvents = [], initialEvent = null, initialTeams = [], initialLeaderboard = {}, initialDonors = {}) {
  const defaultLogo =
    'data:image/svg+xml;charset=UTF-8,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"%3E%3Crect width="320" height="320" fill="%23E2E8F0"/%3E%3Ctext x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="%236B7280" font-family="Arial,Helvetica,sans-serif" font-size="28"%3ETeam%3C/text%3E%3C/svg%3E';

  return {
    events: Array.isArray(initialEvents) ? initialEvents : [],
    activeEvent: initialEvent || null,
    tab: 'peserta',
    teams: Array.isArray(initialTeams) ? initialTeams : [],
    podium: [],
    rankings: [],
    topSupporters: [],
    supporters: [],
    selectedSupporter: null,
    showSupporterModal: false,
    loading: false,
    isEmpty: false,
    error: false,
    errorMessage: '',
    connectionStatus: 'Menghubungkan pembaruan...',

    init() {
      if (this.activeEvent) {
        this.tab = 'peserta';
        this._processLeaderboard(Array.isArray(initialLeaderboard?.entries) ? initialLeaderboard.entries : []);
        this._processDonors(Array.isArray(initialDonors?.entries) ? initialDonors.entries : []);
      }
    },

    selectEvent(event) {
      window.location.href = '/events?event_id=' + encodeURIComponent(event.id);
    },

    backToList() {
      window.location.href = '/events';
    },

    _processLeaderboard(entries) {
      const maxVotes = entries.reduce((sum, team) => Math.max(sum, Number(team.votes || 0)), 0);
      if (this.activeEvent) {
        this.activeEvent.total_votes = entries.reduce((sum, team) => sum + Number(team.votes || 0), 0);
        this.activeEvent.total_teams = entries.length;
      }

      this.podium = entries.slice(0, 3).map((team) => ({
        ...team,
        logo: team.logo_url || defaultLogo,
      }));

      this.rankings = entries.map((team) => ({
        ...team,
        percent: maxVotes > 0 ? Math.round((Number(team.votes || 0) / maxVotes) * 100) : 0,
        logo: team.logo_url || defaultLogo,
      }));
    },

    _processDonors(entries) {
      this.topSupporters = entries.slice(0, 3);
      this.supporters = entries;
      if (this.activeEvent) {
        this.activeEvent.total_supporters = entries.length;
      }
    },

    openSupporter(supporter) {
      this.selectedSupporter = supporter;
      this.showSupporterModal = true;
    },

    closeSupporter() {
      this.showSupporterModal = false;
      this.selectedSupporter = null;
    },

    formatNumber(n) {
      return Number(n).toLocaleString('id-ID');
    },

    formatCurrency(amount) {
      return new Intl.NumberFormat('id-ID', {
        style: 'currency',
        currency: 'IDR',
        maximumFractionDigits: 0,
      }).format(Number(amount || 0));
    },
  };
}

function scoreboard(initial, opts = {}) {
  const { live = true, eventId = null, level = null } = opts;
  return {
    teams: initial.map((t) => ({ ...t, justUpdated: false })),
    _source: null,
    _pollTimer: null,
    _eventId: eventId,
    _level: level,
    _live: live,

    init() {
      if (!this._live) return;
      if (this._eventId) {
        this._connectSSE();
        this._pollTimer = setInterval(() => this._poll(), 30000);
      }
    },

    destroy() {
      this._disconnect();
    },

    _disconnect() {
      if (this._source) {
        this._source.close();
        this._source = null;
      }
      if (this._pollTimer) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
    },

    _connectSSE() {
      this._disconnect();
      const url = `/api/v1/leaderboard/stream?event_id=${encodeURIComponent(this._eventId)}` +
        (this._level ? `&level=${encodeURIComponent(this._level)}` : '');
      const source = new EventSource(url);
      this._source = source;

      source.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === 'leaderboard' && Array.isArray(msg.data?.entries)) {
            this._apply(msg.data.entries);
          }
        } catch (e) {
          // ignore invalid payload
        }
      };
      source.onerror = () => {
        // Browser auto-reconnects EventSource.
      };
      window.addEventListener('beforeunload', () => this._disconnect(), { once: true });
    },

    async _poll() {
      // Fallback only: while SSE is connected the result would be discarded anyway.
      if (this._source?.readyState !== EventSource.CLOSED) return;
      try {
        const res = await fetch(`/api/v1/leaderboard?event_id=${encodeURIComponent(this._eventId)}` +
          (this._level ? `&level=${encodeURIComponent(this._level)}` : ''));
        if (!res.ok) return;
        const body = await res.json();
        if (body.success && Array.isArray(body.data?.entries) && this._source?.readyState === EventSource.CLOSED) {
          this._apply(body.data.entries);
        }
      } catch (e) {
        // ignore network errors
      }
    },

    _apply(entries) {
      const prev = new Map(this.teams.map((t) => [t.id, t.rank]));
      this.teams = entries.map((entry) => {
        const oldRank = prev.get(entry.team_id);
        const trend = oldRank === undefined ? 'same' : entry.rank < oldRank ? 'up' : entry.rank > oldRank ? 'down' : 'same';
        return {
          id: entry.team_id,
          rank: entry.rank,
          name: entry.name,
          school: entry.school || '',
          votes: entry.votes,
          logo_url: entry.logo_url || null,
          photo_url: entry.photo_url || null,
          trend,
          justUpdated: true,
        };
      });
      this.teams.forEach((t) => {
        setTimeout(() => { t.justUpdated = false; }, 650);
      });
    },

    tierAttr(rank) {
      return rank <= 3 ? String(rank) : 'other';
    },

    formatVotes(n) {
      return Number(n).toLocaleString('id-ID');
    },
  };
}

function leaderboardPage(initialEvent = null, initialLeaderboard = []) {
  return {
    event: initialEvent
      ? {
          id: initialEvent.id || null,
          name: initialEvent.name || 'Peringkat',
          status: initialEvent.status || null,
          status_code: initialEvent.status_code || null,
          slug: initialEvent.slug || null,
          is_voting_open: initialEvent.is_voting_open || false,
          description: initialEvent.description || '',
          closes_at: initialEvent.closes_at || null,
          price_per_vote: Number(initialEvent.price_per_vote || 0),
          total_votes: Number(initialEvent.total_votes || 0),
          total_teams: Number(initialEvent.total_teams || 0),
          total_supporters: Number(initialEvent.total_supporters || 0),
        }
      : {
          id: null,
          name: 'Peringkat',
          status: null,
          status_code: null,
          slug: null,
          is_voting_open: false,
          description: '',
          closes_at: null,
          price_per_vote: 0,
          total_votes: 0,
          total_teams: 0,
          total_supporters: 0,
        },
    teams: Array.isArray(initialLeaderboard)
      ? initialLeaderboard.map((team) => ({
          id: team.team_id || team.id,
          rank: team.rank,
          name: team.name,
          school: team.school || '',
          votes: Number(team.votes || 0),
          trend: team.trend || 'same',
          today_votes: Number(team.today_votes || 0),
          percent: 0,
          logo_url: team.logo_url || null,
          slug: team.slug || team.team_id || team.id,
          justUpdated: false,
        }))
      : [],
    teamsRaw: [],
    rankings: [],
    activeTab: 'participants',
    tab: 'teams',
    countdown: {
      days: '00',
      hours: '00',
      minutes: '00',
      seconds: '00',
    },
    podium: [],
    topSupporters: [],
    supporters: [],
    selectedSupporter: null,
    showSupporterModal: false,
    loading: true,
    isEmpty: false,
    error: false,
    errorMessage: '',
    _eventId: initialEvent?.id || null,
    connectionStatus: 'Menghubungkan pembaruan...',
    _sse: null,
    _pollTimer: null,
    _reconnectTimer: null,
    _sseRetry: 0,
    _countdownTimer: null,
    defaultLogo:
      'data:image/svg+xml;charset=UTF-8,%3Csvg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"%3E%3Crect width="320" height="320" fill="%23E2E8F0"/%3E%3Ctext x="50%" y="50%" dominant-baseline="middle" text-anchor="middle" fill="%236B7280" font-family="Arial,Helvetica,sans-serif" font-size="28"%3ETeam%3C/text%3E%3C/svg%3E',

    async init() {
      this.loadingState();
      if (!this._eventId) {
        return this.errorState('Event tidak ditemukan.');
      }

      try {
        await this.loadEvent();
        await this.loadLeaderboard();
        this.startSSE();
        this._startCountdown();
        this.loading = false;
        this.isEmpty = !this.rankings.length;
      } catch (error) {
        this.errorState(error?.message || 'Gagal memuat peringkat.');
      }
    },

    async loadEvent() {
      const response = await this.fetchJson(`/api/v1/events/${encodeURIComponent(this._eventId)}`);
      const data = response.data || response;
      this.event = {
        id: data.id || this.event.id,
        name: data.name || this.event.name,
        status: data.status || this.event.status,
        status_code: data.status_code ?? this.event.status_code,
        slug: data.slug || this.event.slug,
        is_voting_open: data.is_voting_open ?? this.event.is_voting_open,
        description: data.description || this.event.description,
        closes_at: data.closes_at || this.event.closes_at,
        price_per_vote: Number(data.price_per_vote || this.event.price_per_vote),
        total_votes: this.event.total_votes,
        total_teams: this.event.total_teams,
        total_supporters: this.event.total_supporters,
      };
      return data;
    },

    async loadLeaderboard() {
      const response = await this.fetchJson(`/api/v1/leaderboard?event_id=${encodeURIComponent(this._eventId)}`);
      const data = response.data || response;
      if (Array.isArray(data.entries)) {
        const prev = new Map(this.teams.map((team) => [team.id, team.rank]));
        this.teamsRaw = data.entries.map((entry) => {
          const oldRank = prev.get(entry.team_id);
          const trend = oldRank === undefined ? 'same' : entry.rank < oldRank ? 'up' : entry.rank > oldRank ? 'down' : 'same';
          return {
            id: entry.team_id,
            rank: entry.rank,
            name: entry.name,
            school: entry.school || '',
            votes: Number(entry.votes || 0),
            trend,
            today_votes: Number(entry.today_votes || 0),
            percent: 0,
            logo_url: entry.logo_url || null,
            slug: entry.slug || entry.team_id,
            justUpdated: false,
          };
        });
        this.teams = [...this.teamsRaw];
        this._applyDerivedState();
      }
      return data;
    },

    _applyDerivedState() {
      const allTeams = Array.isArray(this.teamsRaw) ? this.teamsRaw : [];
      const maxVotes = allTeams.reduce((sum, team) => Math.max(sum, Number(team.votes || 0)), 0);
      this.event.total_votes = allTeams.reduce((sum, team) => sum + Number(team.votes || 0), 0);
      this.event.total_teams = allTeams.length;
      this.event.total_supporters = Number(this.event.total_supporters || 0);
      this.podium = allTeams.slice(0, 3).map((team) => ({
        ...team,
        logo: team.logo_url || this.defaultLogo,
      }));
      this.rankings = allTeams.map((team) => ({
        ...team,
        percent: maxVotes > 0 ? Math.round((Number(team.votes || 0) / maxVotes) * 100) : 0,
        logo: team.logo_url || this.defaultLogo,
      }));
    },

    _startCountdown() {
      if (this._countdownTimer) {
        clearInterval(this._countdownTimer);
      }
      this._updateCountdown();
      this._countdownTimer = setInterval(() => this._updateCountdown(), 1000);
    },

    _updateCountdown() {
      const closesAt = this.event.closes_at ? new Date(this.event.closes_at) : null;
      if (!closesAt || Number.isNaN(closesAt.getTime())) {
        this.countdown = { days: '00', hours: '00', minutes: '00', seconds: '00' };
        return;
      }
      const diff = closesAt.getTime() - Date.now();
      if (diff <= 0) {
        this.countdown = { days: '00', hours: '00', minutes: '00', seconds: '00' };
        return;
      }
      const totalSeconds = Math.floor(diff / 1000);
      const days = Math.floor(totalSeconds / 86400);
      const hours = Math.floor((totalSeconds % 86400) / 3600);
      const minutes = Math.floor((totalSeconds % 3600) / 60);
      const seconds = totalSeconds % 60;
      const pad = (value) => String(value).padStart(2, '0');
      this.countdown = {
        days: pad(days),
        hours: pad(hours),
        minutes: pad(minutes),
        seconds: pad(seconds),
      };
    },

    fetchJson(url) {
      return fetch(url, { headers: { Accept: 'application/json' } }).then((res) => {
        if (!res.ok) {
          throw new Error(`Request gagal (${res.status})`);
        }
        return res.json();
      });
    },

    startSSE() {
      if (!this._eventId) return;
      this._disconnectSSE();
      const url = `/api/v1/leaderboard/stream?event_id=${encodeURIComponent(this._eventId)}`;
      this._sse = new EventSource(url);
      this._sse.onopen = () => {
        this.connectionStatus = 'Peringkat diperbarui langsung';
        this._sseRetry = 0;
      };

      this._sse.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.type === 'leaderboard' && Array.isArray(payload.data?.entries)) {
            this._applyLeaderboard(payload.data.entries);
          }
        } catch (e) {
          // ignore malformed data
        }
      };

      this._sse.onerror = () => {
        this.connectionStatus = 'Koneksi terputus. Menghubungkan kembali...';
        this._scheduleReconnect();
      };

      this._pollTimer = setInterval(() => this._poll(), 30000);
      window.addEventListener('beforeunload', () => this._disconnectSSE(), { once: true });
    },

    async _poll() {
      // Fallback only: while SSE is connected the result would be discarded anyway.
      if (this._sse?.readyState !== EventSource.CLOSED) return;
      try {
        const response = await this.fetchJson(`/api/v1/leaderboard?event_id=${encodeURIComponent(this._eventId)}`);
        const data = response.data || response;
        if (Array.isArray(data.entries) && this._sse?.readyState === EventSource.CLOSED) {
          this._applyLeaderboard(data.entries);
        }
      } catch (e) {
        // ignore polling errors
      }
    },

    _disconnectSSE() {
      if (this._sse) {
        this._sse.close();
        this._sse = null;
      }
      if (this._pollTimer) {
        clearInterval(this._pollTimer);
        this._pollTimer = null;
      }
      if (this._reconnectTimer) {
        clearTimeout(this._reconnectTimer);
        this._reconnectTimer = null;
      }
    },

    destroy() {
      this._disconnectSSE();
      clearInterval(this._countdownTimer);
    },

    _scheduleReconnect() {
      if (this._reconnectTimer) return;
      this._sseRetry = Math.min(this._sseRetry + 1, 6);
      const delay = 2000 * this._sseRetry;
      this._reconnectTimer = setTimeout(() => {
        this._reconnectTimer = null;
        this.startSSE();
      }, delay);
    },

    _applyLeaderboard(entries) {
      const prev = new Map(this.teamsRaw.map((team) => [team.id, team.rank]));
      this.teamsRaw = entries.map((entry) => {
        const oldRank = prev.get(entry.team_id);
        const trend = oldRank === undefined ? 'same' : entry.rank < oldRank ? 'up' : entry.rank > oldRank ? 'down' : 'same';
        return {
          id: entry.team_id,
          rank: entry.rank,
          name: entry.name,
          school: entry.school || '',
          votes: Number(entry.votes || 0),
          trend,
          today_votes: Number(entry.today_votes || 0),
          percent: 0,
          logo_url: entry.logo_url || null,
          slug: entry.slug || entry.team_id,
          justUpdated: true,
        };
      });
      this.teams = [...this.teamsRaw];
      this._applyDerivedState();
      this.teams.forEach((team) => {
        setTimeout(() => {
          team.justUpdated = false;
        }, 650);
      });
    },

    openSupporter(supporter) {
      this.selectedSupporter = supporter;
      this.showSupporterModal = true;
    },

    closeSupporter() {
      this.showSupporterModal = false;
      this.selectedSupporter = null;
    },

    loadingState() {
      this.loading = true;
      this.error = false;
      this.errorMessage = '';
      this.isEmpty = false;
    },

    errorState(message) {
      this.loading = false;
      this.error = true;
      this.errorMessage = message || 'Terjadi kesalahan.';
      this.isEmpty = false;
    },

    formatNumber(n) {
      return Number(n).toLocaleString('id-ID');
    },

    formatCurrency(amount) {
      return new Intl.NumberFormat('id-ID', {
        style: 'currency',
        currency: 'IDR',
        maximumFractionDigits: 0,
      }).format(Number(amount || 0));
    },
  };
}
