# Perbaikan Bug Kritis dan Rekonsiliasi Duitku — RVD

## Tujuan

Memperbaiki integrasi Duitku pada repository `rvd-2nd` berdasarkan hasil audit teknis.

Fokus perubahan:

1. Memastikan validasi signature callback tidak mengalami NameError.
2. Menambahkan fungsi `transactionStatus` untuk rekonsiliasi transaksi.
3. Mengintegrasikan rekonsiliasi dengan penanganan timeout inquiry secara aman.
4. Menambahkan pengujian regresi tanpa mengubah alur pembayaran yang sudah berfungsi.

Jangan melakukan refactor besar, mengganti payment gateway, atau mengubah struktur database tanpa kebutuhan yang terbukti.

## Step 1. Perbaiki Signature Callback

File: `app/services/duitku_service.py`

Fungsi: `verify_callback_signature()`

Ketentuan:

- Tetap menggunakan `hmac.compare_digest()` untuk membandingkan signature.
- Jangan mengubah urutan parameter signature.
- Jangan mengubah algoritma inquiry yang sudah benar.
- Jangan menelan `NameError` atau exception lain menggunakan fallback yang menganggap signature valid.

Formula callback: `HMAC-SHA256(merchantCode + amount + merchantOrderId, apiKey)`

## Step 2. Tambahkan Signature Khusus transactionStatus

File: `app/services/duitku_service.py`

Jangan menggunakan `generate_signature()` inquiry dengan `payment_amount=0`, karena formula inquiry menyertakan nominal, sedangkan formula status transaksi tidak.

Helper:

```python
def generate_transaction_status_signature(
    merchant_code: str,
    merchant_order_id: str,
    api_key: str,
) -> str:
    signing_string = f"{merchant_code}{merchant_order_id}"
    return hmac.new(
        api_key.encode("utf-8"),
        signing_string.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
```

Gunakan helper tersebut khusus untuk permintaan `transactionStatus`.

## Step 3. Implementasikan check_transaction_status()

File: `app/services/duitku_service.py`

Fungsi asynchronous `check_transaction_status()` dengan ketentuan:

1. Menerima `merchant_order_id`.
2. Mengambil merchant code dan API key dari konfigurasi yang sudah ada.
3. Memastikan kredensial tersedia sebelum mengirim request.
4. Menggunakan endpoint sesuai environment yang aktif.
5. Mengirim HTTP POST JSON dengan `merchantCode`, `merchantOrderId`, dan signature khusus transactionStatus.
6. Menggunakan timeout HTTP yang terkonfigurasi.
7. Memvalidasi HTTP status dan struktur respons JSON.
8. Mengembalikan respons terstruktur agar service pemanggil dapat memetakan status.
9. Membedakan timeout, HTTP error, respons JSON invalid, dan respons bisnis yang tidak sukses.
10. Tidak mencatat API key, signature lengkap, atau informasi sensitif ke log.

Endpoint:

- Sandbox: `https://sandbox.duitku.com/webapi/api/merchant/transactionStatus`
- Production: `https://passport.duitku.com/webapi/api/merchant/transactionStatus`

Jangan menganggap respons HTTP 200 sebagai bukti pembayaran sukses. Status bisnis dalam respons harus dipetakan sesuai dokumentasi Duitku.

## Step 4. Integrasikan Rekonsiliasi Inquiry Timeout

File:

- `app/services/duitku_service.py`
- `app/services/payment_service.py`

Jika inquiry mengalami timeout:

1. Pertahankan transaksi internal yang sudah tersimpan.
2. Jangan langsung mengubah transaksi menjadi gagal permanen.
3. Jangan membuat order baru secara otomatis.
4. Gunakan `merchant_order_id` yang sama untuk rekonsiliasi.
5. Panggil `check_transaction_status()` hanya jika transaksi dan kondisi request memenuhi syarat rekonsiliasi.
6. Jika status belum dapat dipastikan, pertahankan kondisi pending atau status internal yang sudah tersedia untuk pemeriksaan lanjutan.
7. Jangan memberikan vote hanya berdasarkan timeout, respons inquiry, atau redirect pengguna.

`transactionStatus` mungkin tidak menemukan transaksi apabila inquiry belum berhasil dibuat di sisi Duitku. Jika status tidak ditemukan atau respons ambigu, jangan menyimpulkan bahwa order aman untuk dibuat ulang tanpa kebijakan retry yang sesuai dokumentasi.

Jangan menjalankan rekonsiliasi berulang tanpa batas. Jika diperlukan, gunakan mekanisme retry yang sudah ada dan batas percobaan yang terkontrol.

Jangan membuat sistem background worker baru jika belum diperlukan oleh arsitektur saat ini.

## Step 5. Pertahankan Callback dan Idempotensi

File:

- `app/routers/payments.py`
- `app/services/payment_service.py`
- `app/services/vote_service.py`

Pastikan perbaikan tidak merusak mekanisme berikut:

- Parsing callback JSON dan form-urlencoded yang sudah tersedia.
- Validasi merchant code, signature, order ID, nominal, dan reference.
- Pessimistic locking pada payment.
- Idempotensi transaksi SUCCESS.
- Unique constraint `VoteLog.payment_id`.
- Grant vote yang atomik.

Callback valid harus dapat diproses kembali setelah bug signature diperbaiki.

Callback duplikat tidak boleh menambah vote. Callback gagal yang datang setelah pembayaran sukses tidak boleh menurunkan status SUCCESS.

Jangan menjadikan pemanggilan `transactionStatus` sebagai pengganti validasi callback.

## Step 6. Tambahkan Pengujian Regresi

File: `tests/test_duitku.py`

Pengujian:

### A. Callback signature tidak mengalami NameError

- Panggil `verify_callback_signature()` dengan input valid.
- Pastikan fungsi mengembalikan boolean.
- Pastikan tidak terjadi `NameError`.

### B. Signature callback valid dan invalid

- Signature valid menghasilkan `True`.
- Signature salah menghasilkan `False`.
- Perubahan nominal atau order ID membuat signature tidak cocok.

### C. Signature transactionStatus

- Pastikan signing string hanya berisi merchant code dan merchant order ID.
- Pastikan nominal tidak ikut ditambahkan.
- Pastikan output berupa hexadecimal lowercase.

### D. Request transactionStatus

Gunakan HTTP mock, bukan request sungguhan ke Duitku.

- Pastikan URL mengikuti environment.
- Pastikan metode POST dan payload benar.
- Pastikan timeout ditangani.
- Pastikan HTTP error dan JSON invalid tidak dianggap sebagai pembayaran sukses.

### E. Rekonsiliasi timeout

- Inquiry timeout mempertahankan transaksi internal.
- Tidak ada order duplikat otomatis.
- Status yang tidak diketahui tidak menghasilkan grant vote.
- Status sukses hanya diproses melalui mekanisme verifikasi yang tepercaya dan idempoten.

### F. Callback paralel

Pertahankan pengujian `test_parallel_duitku_callbacks_grant_votes_once`.

Pastikan callback paralel tetap menghasilkan tepat satu grant vote setelah perbaikan.

## Step 7. Batasan Perubahan

Jangan:

- Mengganti signature inquiry yang sudah benar.
- Menghapus integrasi payment gateway lain.
- Membuat model Payment atau endpoint callback duplikat.
- Mengubah schema database tanpa kebutuhan yang terbukti.
- Menganggap status redirect sebagai bukti pembayaran.
- Menganggap timeout berarti pembayaran gagal.
- Mengirim request pembayaran produksi untuk keperluan pengujian.
- Menyatakan rekonsiliasi berhasil tanpa bukti hasil respons yang valid.

## Step 8. Laporan Setelah Implementasi

Laporkan:

1. File yang diubah.
2. Perbaikan variabel signature callback.
3. Implementasi signature dan request `transactionStatus`.
4. Perilaku sistem saat inquiry timeout.
5. Hasil pengujian regresi yang benar-benar dijalankan.
6. Pengujian yang belum dijalankan.
7. Konfigurasi deployment yang perlu diperiksa.

Jangan mengklaim seluruh integrasi siap production hanya karena bug signature callback sudah diperbaiki.

Prioritas pengerjaan: perbaiki bug signature callback terlebih dahulu, tambahkan tes regresi, lalu implementasikan rekonsiliasi transaksi sebagai perubahan terpisah.
