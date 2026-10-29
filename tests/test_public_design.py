"""Public rendering and event-specific leaderboard regression checks."""
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app.enums.event_status import EventStatus
from app.models.event import Event
from app.routers import pages


@pytest.mark.parametrize('opens_days,closes_days,is_open,label', [
    (1, 2, False, 'Voting belum dimulai'),
    (-1, 1, True, 'Voting dibuka'),
    (-2, -1, False, 'Voting ditutup'),
    (None, None, True, 'Voting dibuka'),
    (None, -1, False, 'Voting ditutup'),
])
def test_public_event_label_matches_voting_window(opens_days, closes_days, is_open, label):
    now = datetime.now(timezone.utc)
    event = Event(id='scheduled-event', name='Scheduled Event', status=EventStatus.VOTING_OPEN,
                  opens_at=now + timedelta(days=opens_days) if opens_days is not None else None,
                  closes_at=now + timedelta(days=closes_days) if closes_days is not None else None)
    data = pages._event_dict(event)
    assert data['is_voting_open'] is is_open
    assert data['status'] == label
    assert data['status_code'] == EventStatus.VOTING_OPEN.value


@pytest.mark.parametrize('status', [EventStatus.PUBLISHED, EventStatus.VOTING_CLOSED,
                                  EventStatus.FINISHED, EventStatus.ARCHIVED])
def test_unscheduled_public_event_preserves_lifecycle_label(status):
    data = pages._event_dict(Event(id='event', name='Event', status=status))
    assert data['status'] == pages._STATUS_LABELS[status]
    assert data['is_voting_open'] is False


def request(path='/'):
    return Request({'type': 'http', 'method': 'GET', 'path': path,
                    'headers': [], 'query_string': b'', 'scheme': 'http',
                    'server': ('test', 80)})


@pytest.mark.parametrize('name', ['landing', 'events', 'leaderboard', 'voting_closed'])
def test_public_templates_render(name):
    event = {'id': 'event-example', 'name': 'Creative Awards', 'description': '',
             'is_voting_open': True, 'status': 'Voting dibuka', 'banner_url': None,
             'closes_at': None, 'price_per_vote': 1000}
    team = {'id': 'participant-example', 'name': 'Example Participant', 'votes': 12}
    html = pages.templates.get_template(f'pages/{name}.html').render(
        request=request(), event=event, all_events=[event], leaderboard=[], team=team)
    assert '/static/css/public.css' in html
    assert 'public-site' in html
    assert '/static/images/raja-voting-logo.png' in html
    assert 'via Midtrans' not in html
    assert 'id="main-content"' in html
    if name == 'landing':
        assert 'class="public-header shell"' in html
        assert 'home-product-track' in html
        assert 'Temukan event' in html
        assert '<h1>Platform Voting Digital</h1>' not in html
        assert 'Satu suara.' not in html
        assert 'Event yang sedang berjalan' in html
        assert all(label in html for label in ('Voting', 'Peringkat', 'Peserta', 'Bantuan'))
        assert 'Event &amp; Peserta' not in html


def test_public_navigation_uses_routes_supported_by_current_app():
    html = pages.templates.get_template('public_base.html').render(request=request())
    assert 'href="/events"' in html
    assert 'href="/support"' in html
    assert 'href="/leaderboard"' in html


def test_support_template_renders_without_placeholders():
    html = pages.templates.get_template('pages/support.html').render(
        request=request('/support'), support_email='', support_phone='', support_hours='')
    assert '/static/css/public.css' in html
    assert 'public-site' in html
    assert '/static/images/raja-voting-logo.png' in html
    assert 'via Midtrans' not in html
    assert 'id="main-content"' in html
    assert 'Pusat Bantuan' in html
    assert 'Raja Voting Digital' in html
    assert 'Masukkan email resmi' not in html
    assert 'Masukkan nomor kontak aktif' not in html
    assert 'Masukkan jam operasional' not in html
    assert 'Tentukan apakah pesanan' not in html
    assert 'Ketentuan final yang perlu ditetapkan' not in html
    assert 'Kebijakan Privasi' not in html


def test_support_page_has_content_anchors_and_accessible_faq():
    html = pages.templates.get_template('pages/support.html').render(
        request=request('/support'), support_email='', support_phone='', support_hours='')
    for section_id in ('tentang', 'kontak', 'syarat', 'refund', 'faq'):
        assert f'id="{section_id}"' in html
        assert f'href="#{section_id}"' in html
    assert 'mailto:rajadigital.group@gmail.com' in html
    assert 'https://wa.me/6285602623817' in html
    assert '+62 856-0262-3817' in html
    assert 'Kebijikan' not in html
    assert 'Refund Guaranteed' not in html
    assert html.count('role="region"') == 13
    assert html.count('aria-controls="faq-') == 13
    assert 'x-cloak' in html


def test_support_terms_and_refund_follow_scannable_structure():
    html = pages.templates.get_template('pages/support.html').render(
        request=request('/support'), support_email='', support_phone='', support_hours='')
    terms = html[html.index('<section id="syarat"'):html.index('<section id="refund"')]
    refund = html[html.index('<section id="refund"'):html.index('<section id="faq"')]
    headings = [
        re.sub(r'<[^>]+>', '', block.split('</h3>')[0]).strip()
        for block in terms.split('<h3 class="support-terms-sub">')[1:4]
    ]
    assert headings == [
        'Ketentuan Penggunaan',
        'Ketentuan Pemesanan dan Pembayaran',
        'Pembatalan Pesanan',
    ]
    refund_headings = [
        re.sub(r'<[^>]+>', '', block.split('</h3>')[0]).strip()
        for block in refund.split('<h3 class="support-terms-sub">')[1:4]
    ]
    assert refund_headings == ['Ketentuan Refund', 'Kondisi Pengajuan', 'Cara Mengajukan Refund']
    assert terms.count('class="support-terms-sub__icon"') == 3
    assert refund.count('class="support-terms-sub__icon"') == 3
    assert '<ol' not in html
    assert 'support-terms-list--checks' in terms
    assert 'support-terms-list--conditions' in refund
    assert 'support-terms-list--steps' in refund
    usage = terms.split('support-terms-list--checks">')[1].split('</ul>')[0]
    assert usage.count('<li>') == 8
    assert terms.count('class="support-info-card"') == 4
    for title in ('Harga dan Rincian Pesanan', 'Verifikasi Pembayaran', 'Bukti Transaksi', 'Hindari Pembayaran Ganda'):
        assert f'<h4 class="support-info-card__title">{title}</h4>' in terms
    assert 'Pembayaran yang masih tertunda belum dianggap berhasil.' in terms
    assert 'pembatalan mungkin tidak dapat dilakukan' in terms
    assert refund.count('<li><strong>') == 5
    assert '<strong>Pembayaran gagal atau kedaluwarsa.</strong>' in refund
    steps = refund.split('support-terms-list--steps">')[1].split('</ul>')[0]
    assert steps.count('<li>') == 4
    for step in (
        'Hubungi tim bantuan melalui',
        'Sertakan nomor pesanan, waktu transaksi, metode pembayaran, dan bukti pembayaran jika tersedia.',
        'Jelaskan kendala yang terjadi secara singkat dan jelas.',
        'Tunggu hasil pemeriksaan dari tim bantuan.',
    ):
        assert step in steps
    assert 'Penting: Keamanan Data' in refund
    assert 'Jangan pernah memberikan PIN, kata sandi, atau kode OTP' in refund
    assert 'role="note"' in refund
    assert 'menyalahankan' not in html
    assert 'pemb karena' not in html


@pytest.mark.parametrize('name', ['landing', 'events', 'leaderboard'])
def test_public_empty_states_render(name):
    html = pages.templates.get_template(f'pages/{name}.html').render(
        request=request(), event=None, all_events=[], leaderboard=[])
    assert 'public-site' in html
    assert '/static/images/raja-voting-logo.png' in html
    assert 'via Midtrans' not in html
    assert 'href="/events"' in html


@pytest.mark.parametrize('name,path', [
    ('dashboard', '/admin'), ('events', '/admin/events'),
    ('transactions', '/admin/transactions'), ('audit_logs', '/admin/audit-logs'),
    ('settings', '/admin/settings'), ('leaderboard', '/admin/leaderboard'),
    ('progress', '/admin/progress'),
])
def test_admin_uses_the_shared_theme(name, path):
    html = pages.templates.get_template(f'pages/admin_{name}.html').render(
        request=request(path), event=None, leaderboard=[])
    assert '/static/css/public.css' in html
    assert '/static/css/admin.css' in html
    assert 'admin-sidebar' in html
    assert 'public-header' not in html
    assert html.count('id="sidebar"') == 1
    assert html.count('aria-current="page"') == 1
    assert 'id="admin-main"' in html


def test_admin_login_has_labeled_credentials_and_error_feedback():
    html = pages.templates.get_template('pages/admin_login.html').render(
        request=request('/admin/login'))
    assert '/static/css/admin.css' in html
    assert 'autocomplete="username"' in html
    assert 'autocomplete="current-password"' in html
    assert 'for="email"' in html
    assert 'for="password"' in html
    assert 'role="alert"' in html
