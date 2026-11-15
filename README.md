# Bacakomik API by Nat

Unofficial scraper API untuk **https://bacakomik.my** (WordPress + tema komikcast6).

**By: Nat · v1.0.0**

## Endpoint

| Method | Path | Keterangan |
|--------|------|------------|
| GET | `/health` | Status |
| GET | `/api/latest?page=1` | Komik terbaru |
| GET | `/api/popular?page=1` | Populer |
| GET | `/api/colored?page=1` | Komik berwarna |
| GET | `/api/search?q=` | Cari judul |
| GET | `/api/genres` | Daftar genre |
| GET | `/api/genre/{slug}?page=1` | Komik per genre |
| GET | `/api/detail/{slug}` | Detail + sinopsis + chapter |
| GET | `/api/chapter?slug=&chapter=` | Gambar chapter |
| GET | `/api/chapter?url=` | Gambar via URL chapter |
| GET | `/api/img?url=` | Proxy gambar CDN |

## Contoh

```bash
curl "https://YOUR.vercel.app/api/latest?page=1"
curl "https://YOUR.vercel.app/api/search?q=nano"
curl "https://YOUR.vercel.app/api/detail/nano-machine"
curl "https://YOUR.vercel.app/api/chapter?slug=nano-machine&chapter=338"
```

## Deploy Vercel

1. Buat repo GitHub, upload semua file di folder ini (root repo)
2. Vercel → New Project → Import → Deploy
3. Opsional env:

| Key | Default |
|-----|---------|
| `BACAKOMIK_BASE` | `https://bacakomik.my` |
| `BACAKOMIK_TIMEOUT` | `28` |

## Catatan

- Situs di belakang **Cloudflare**. Saat ini HTML sering lolos tanpa challenge; jika CF diperketat, endpoint bisa 403 dari IP datacenter (Vercel).
- Gambar chapter di CDN eksternal → pakai `/api/img?url=` bila hotlink diblok.
- Konten milik Bacakomik; API ini hanya wrapper scrape untuk keperluan pribadi/edukasi.
