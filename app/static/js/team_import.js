/**
 * Shared "Import peserta dari Excel" controller (admin only).
 *
 * Expects the markup from templates/partials/team_import_modal.html rendered
 * once per page, plus window.adminEscapeHTML (app/static/js/admin.js).
 *
 * Usage:
 *   const importer = TeamImport.init({
 *     modalId: 'team-import-modal',     // id of the rendered modal root
 *     getEventId: () => currentEventId, // event resolver (required)
 *     showEventSelect: true,            // show the in-modal event picker
 *     onImported: () => loadTeams(),    // called after a successful import
 *   });
 *   importer.open();                    // shows the modal and resets state
 */
(function() {
  'use strict';

  const API_BASE = '/api/v1';
  const MAX_FILE_BYTES = 1024 * 1024;

  function init(config) {
    const root = document.getElementById(config.modalId);
    if (!root) throw new Error('Import modal not found: ' + config.modalId);

    const role = function(name) { return root.querySelector('[data-import-role="' + name + '"]'); };
    const eventSelect = role('event-select');
    const eventSelectWrap = root.querySelector('[data-import-event-select-wrap]');
    const fileInput = role('file');
    const errorEl = role('error');
    const summaryEl = role('summary');
    const previewWrap = role('preview-wrap');
    const previewBody = role('preview-body');
    const confirmBtn = role('confirm-btn');
    const previewBtn = role('preview-btn');
    const templateBtn = role('template-btn');

    let report = null;

    const esc = window.adminEscapeHTML || function(v) { return String(v); };

    function showError(message) {
      errorEl.textContent = message;
      errorEl.classList.remove('hidden');
    }

    function hideError() {
      errorEl.textContent = '';
      errorEl.classList.add('hidden');
    }

    function currentEventId() {
      if (config.getEventId) return config.getEventId() || '';
      return eventSelect ? eventSelect.value : '';
    }

    function reset() {
      report = null;
      fileInput.value = '';
      hideError();
      summaryEl.classList.add('hidden');
      previewWrap.classList.add('hidden');
      confirmBtn.disabled = true;
    }

    function open() {
      reset();
      // Show the in-modal event select only when the page asks for it; pages
      // with a fixed event hide it even though they pass getEventId.
      if (config.showEventSelect) {
        eventSelectWrap.classList.remove('hidden');
      } else {
        eventSelectWrap.classList.add('hidden');
      }
      root.classList.remove('hidden');
    }

    function close() {
      root.classList.add('hidden');
      reset();
    }

    async function authorizedFetch(path, options) {
      const token = localStorage.getItem('wvc_admin_token') || '';
      const res = await fetch(API_BASE + path, Object.assign({}, options, {
        headers: Object.assign({ 'Authorization': 'Bearer ' + token }, options.headers || {}),
      }));
      if (res.status === 401 || res.status === 403) {
        localStorage.removeItem('wvc_admin_token');
        window.location.href = '/admin/login';
        return null;
      }
      return res;
    }

    async function downloadTemplate() {
      const eventId = currentEventId();
      if (!eventId) { showError('Pilih event terlebih dahulu.'); return; }
      hideError();
      try {
        const res = await authorizedFetch('/admin/events/' + eventId + '/teams/import/template', {});
        if (!res) return;
        if (!res.ok) throw new Error('Gagal mengunduh template');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'template-import-peserta.xlsx';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        showError(e.message);
      }
    }

    function renderReport(data) {
      report = data;
      const badge = {
        valid: '<span class="px-2 py-0.5 rounded text-xs bg-green-100 text-green-700">valid</span>',
        invalid: '<span class="px-2 py-0.5 rounded text-xs bg-red-100 text-red-700">invalid</span>',
        duplicate: '<span class="px-2 py-0.5 rounded text-xs bg-amber-100 text-amber-700">duplikat</span>',
      };

      const notes = [];
      if (data.blocking_message) notes.push(esc(data.blocking_message));
      if (data.skipped_empty_rows && data.skipped_empty_rows.length) {
        notes.push('Baris kosong dilewati: ' + esc(data.skipped_empty_rows.join(', ')));
      }
      (data.warnings || []).forEach(function(w) { notes.push(esc(w)); });

      summaryEl.innerHTML =
        'Total baris: <strong>' + data.total_rows + '</strong> · ' +
        '<span class="text-green-700">valid: ' + data.valid_count + '</span> · ' +
        '<span class="text-red-700">invalid: ' + data.invalid_count + '</span> · ' +
        '<span class="text-amber-700">duplikat di file: ' + data.duplicate_in_file_count + '</span> · ' +
        '<span class="text-amber-700">sudah ada di event: ' + data.duplicate_existing_count + '</span>' +
        (notes.length ? '<div class="mt-1 text-xs text-gray-500">' + notes.join(' · ') + '</div>' : '');
      summaryEl.classList.remove('hidden');

      previewBody.innerHTML = (data.rows || []).map(function(row) {
        const reasons = (row.errors || []).map(function(e) { return esc(e.message); }).join('; ');
        return '<tr class="border-b last:border-0">' +
          '<td class="px-2 py-1.5">' + row.row_number + '</td>' +
          '<td class="px-2 py-1.5">' + esc(row.data.nama_tim) + '</td>' +
          '<td class="px-2 py-1.5">' + esc(row.data.sekolah || '') + '</td>' +
          '<td class="px-2 py-1.5">' + esc(row.data.kategori_label || row.data.kategori || '') + '</td>' +
          '<td class="px-2 py-1.5">' + (badge[row.status] || esc(row.status)) + '</td>' +
          '<td class="px-2 py-1.5 text-xs text-red-600">' + reasons + '</td>' +
        '</tr>';
      }).join('');
      previewWrap.classList.remove('hidden');

      confirmBtn.disabled = !data.can_confirm;
    }

    async function previewFile() {
      hideError();
      const eventId = currentEventId();
      if (!eventId) { showError('Pilih event terlebih dahulu.'); return; }
      const file = fileInput.files && fileInput.files[0];
      if (!file) { showError('Pilih file .xlsx terlebih dahulu.'); return; }
      if (file.size > MAX_FILE_BYTES) { showError('Ukuran file melebihi 1 MiB.'); return; }

      previewBtn.disabled = true;
      try {
        const formData = new FormData();
        formData.append('file', file, file.name);
        const res = await authorizedFetch('/admin/events/' + eventId + '/teams/import/preview', {
          method: 'POST',
          body: formData,
        });
        if (!res) return;
        const body = await res.json().catch(function() { return {}; });
        if (!res.ok || !body.success) {
          throw new Error((body && body.message) || 'Gagal memeriksa file');
        }
        renderReport(body.data);
      } catch (e) {
        showError(e.message);
      } finally {
        previewBtn.disabled = false;
      }
    }

    async function confirmFile() {
      if (!report || !report.can_confirm) return;
      const eventId = currentEventId();
      const file = fileInput.files && fileInput.files[0];
      if (!eventId || !file) return;
      if (!confirm('Impor ' + report.valid_count + ' tim ke event ini?')) return;

      hideError();
      confirmBtn.disabled = true;
      try {
        const formData = new FormData();
        formData.append('file', file, file.name);
        formData.append('sha256', report.sha256);
        const res = await authorizedFetch('/admin/events/' + eventId + '/teams/import/confirm', {
          method: 'POST',
          body: formData,
        });
        if (!res) return;
        const body = await res.json().catch(function() { return {}; });
        if (!res.ok || !body.success) {
          if (body && body.data && body.data.rows) {
            // Re-render with the server-side problem list so the admin can fix the file.
            renderReport(Object.assign({}, report, body.data, { can_confirm: false }));
          }
          throw new Error((body && body.message) || 'Impor gagal');
        }
        reset();
        if (typeof config.onImported === 'function') config.onImported();
      } catch (e) {
        showError(e.message);
        confirmBtn.disabled = report ? !report.can_confirm : true;
      }
    }

    templateBtn.addEventListener('click', downloadTemplate);
    previewBtn.addEventListener('click', previewFile);
    confirmBtn.addEventListener('click', confirmFile);
    root.querySelectorAll('[data-import-close]').forEach(function(el) {
      el.addEventListener('click', close);
    });

    return { open: open, close: close, reset: reset };
  }

  window.TeamImport = { init: init };
})();
