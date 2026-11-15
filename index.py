"""
Bacakomik API by Nat — v1.0.0
Sumber: https://bacakomik.my  (WordPress + tema komikcast6)

Scrape HTML (bukan API resmi):
  - List   : /komik-terbaru/ , /komik-populer/ , /komik-berwarna/
  - Search : /?s=
  - Genre  : /genres/{slug}/
  - Detail : /komik/{slug}/
  - Chapter: /{slug}-chapter-{n}/
"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin, urlparse, parse_qs, urlencode, urlunparse

import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from mangum import Mangum

APP_VERSION = "1.0.0"
BASE = os.environ.get("BACAKOMIK_BASE", "https://bacakomik.my").rstrip("/")
TIMEOUT = float(os.environ.get("BACAKOMIK_TIMEOUT", "28"))

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "id-ID,id;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
    "Referer": BASE + "/",
}

# pola iklan / tracking di URL gambar
AD_RE = re.compile(
    r"(?:banner|casino|slot|doubleclick|adservice|/iklan/|promo|tracking|pixel|1x1)",
    re.I,
)
SVG_PLACEHOLDER_RE = re.compile(r"data:image/svg|\.svg(?:\?|$)", re.I)
CHAPTER_NUM_RE = re.compile(r"chapter[-_]?(\d+(?:\.\d+)?)", re.I)
SLUG_FROM_KOMIK_RE = re.compile(r"/komik/([^/]+)/?")
SLUG_FROM_CH_RE = re.compile(r"https?://[^/]+/([^/]+)-chapter-", re.I)

app = FastAPI(
    title="Bacakomik API by Nat",
    description="Unofficial scraper API untuk bacakomik.my",
    version=APP_VERSION,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── helpers ──────────────────────────────────────────────

def _abs(url: Optional[str]) -> str:
    if not url:
        return ""
    url = str(url).strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http"):
        return url
    return urljoin(BASE + "/", url.lstrip("/"))


def _clean_img(url: Optional[str]) -> str:
    """Ambil URL gambar valid; buang placeholder SVG & upgrade ke https."""
    if not url:
        return ""
    url = str(url).strip()
    if not url or SVG_PLACEHOLDER_RE.search(url):
        return ""
    if url.startswith("//"):
        url = "https:" + url
    if url.startswith("http://"):
        url = "https://" + url[7:]
    # buang resize query WordPress photon yang merusak rasio (opsional kecil)
    if "i0.wp.com" in url or "i1.wp.com" in url or "i2.wp.com" in url:
        try:
            p = urlparse(url)
            q = parse_qs(p.query)
            q.pop("resize", None)
            q.pop("w", None)
            q.pop("h", None)
            url = urlunparse(p._replace(query=urlencode({k: v[0] for k, v in q.items()}, doseq=False)))
        except Exception:
            pass
    return url


def _img_from_tag(img) -> str:
    if not img:
        return ""
    for attr in ("data-lazy-src", "data-src", "data-original", "data-srcset", "srcset", "src"):
        val = img.get(attr)
        if not val:
            continue
        # srcset: ambil URL pertama
        if "srcset" in attr:
            val = val.split(",")[0].strip().split(" ")[0]
        cleaned = _clean_img(val)
        if cleaned:
            return cleaned
    return ""


def _norm_type(raw: str) -> str:
    t = (raw or "").strip().upper()
    if not t:
        return "COMIC"
    if "MANHWA" in t:
        return "MANHWA"
    if "MANHUA" in t:
        return "MANHUA"
    if "MANGA" in t:
        return "MANGA"
    return t[:20] or "COMIC"


def _slug_from_url(url: str) -> str:
    if not url:
        return ""
    m = SLUG_FROM_KOMIK_RE.search(url)
    if m:
        return m.group(1).strip("/")
    m = SLUG_FROM_CH_RE.search(url)
    if m:
        return m.group(1).strip("/")
    # path terakhir
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1] if path else ""


def _item(judul: str, slug: str, thumb: str, type_: str = "", chapter: str = "", **extra) -> Dict[str, Any]:
    slug = (slug or "").strip()
    return {
        "judul": (judul or slug or "Tanpa judul").strip(),
        "title": (judul or slug or "Tanpa judul").strip(),
        "slug": slug,
        "thumbnail": _clean_img(thumb),
        "sampul": _clean_img(thumb),
        "type": _norm_type(type_),
        "chapter": chapter or None,
        "source": "bacakomik",
        "url": f"{BASE}/komik/{slug}/" if slug else "",
        **extra,
    }


async def fetch_html(path: str, referer: Optional[str] = None) -> str:
    url = path if path.startswith("http") else f"{BASE}{path if path.startswith('/') else '/' + path}"
    headers = dict(BROWSER_HEADERS)
    headers["Referer"] = referer or (BASE + "/")
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        res = await client.get(url, headers=headers)
        if res.status_code == 403:
            raise HTTPException(
                status_code=403,
                detail="Cloudflare memblokir permintaan (403). Coba lagi nanti / dari jaringan lain.",
            )
        if res.status_code == 404:
            raise HTTPException(status_code=404, detail="Halaman tidak ditemukan di Bacakomik.")
        if res.status_code >= 400:
            raise HTTPException(status_code=502, detail=f"Upstream HTTP {res.status_code}")
        text = res.text or ""
        if "Just a moment" in text or "cf-browser-verification" in text:
            raise HTTPException(
                status_code=403,
                detail="Cloudflare challenge aktif. Scrape dari Vercel mungkin tertahan.",
            )
        return text


def parse_list_html(html: str) -> List[Dict[str, Any]]:
    """Parse kartu .animepost di halaman list/search/genre."""
    soup = BeautifulSoup(html, "html.parser")
    out: List[Dict[str, Any]] = []
    seen = set()

    posts = soup.select(".animepost")
    if not posts:
        # fallback: tautan /komik/
        for a in soup.select("a[href*='/komik/']"):
            href = a.get("href") or ""
            slug = _slug_from_url(href)
            if not slug or slug in seen:
                continue
            seen.add(slug)
            title = (a.get("title") or a.get_text(" ", strip=True) or slug).replace("Komik ", "").strip()
            img = a.select_one("img")
            out.append(_item(title, slug, _img_from_tag(img)))
        return out

    for post in posts:
        a = post.select_one("a[href*='/komik/']") or post.select_one("a[href]")
        if not a:
            continue
        href = a.get("href") or ""
        slug = _slug_from_url(href)
        if not slug or slug in seen:
            continue
        seen.add(slug)

        title_el = post.select_one(".tt h4, .tt, h4, h3")
        title = ""
        if title_el:
            title = title_el.get_text(" ", strip=True)
        if not title:
            title = (a.get("title") or "").replace("Komik ", "").strip()
        title = re.sub(r"\s+", " ", title).strip() or slug

        img = post.select_one("img")
        thumb = _img_from_tag(img)

        # typeflag: <span class="typeflag Manhwa"></span>
        type_ = ""
        flag = post.select_one(".typeflag")
        if flag:
            classes = flag.get("class") or []
            for c in classes:
                if c.lower() in ("manhwa", "manhua", "manga"):
                    type_ = c
                    break
            if not type_ and len(classes) > 1:
                type_ = classes[-1]

        # chapter terbaru di kartu
        ch_a = post.select_one(".lsch a, .adds a[href*='chapter']")
        ch_text = ""
        if ch_a:
            ch_text = re.sub(r"\s+", " ", ch_a.get_text(" ", strip=True))

        out.append(_item(title, slug, thumb, type_, chapter=ch_text))
    return out


def parse_detail_html(html: str, slug: str) -> Dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")

    # judul
    h1 = soup.select_one("h1.entry-title, h1")
    judul = ""
    if h1:
        judul = h1.get_text(" ", strip=True)
        judul = re.sub(r"^Komik\s+", "", judul, flags=re.I).strip()

    # sampul: og:image dulu, lalu .thumb img
    thumb = ""
    og = soup.select_one('meta[property="og:image"]')
    if og and og.get("content"):
        thumb = _clean_img(og.get("content"))
    if not thumb:
        thumb = _img_from_tag(soup.select_one(".thumb img, .ime img, .komik_info-content-thumbnail img"))

    # sinopsis
    sinopsis = ""
    for sel in (".desc", ".entry-content .desc", ".shortcsc", ".entry-content"):
        el = soup.select_one(sel)
        if not el:
            continue
        text = el.get_text("\n", strip=True)
        # buang label "Sinopsis" di depan
        text = re.sub(r"^Sinopsis\s*", "", text, flags=re.I).strip()
        if len(text) > 40:
            sinopsis = text
            break

    # type / status dari .infox / .spe
    type_ = ""
    status = ""
    info_text = ""
    for sel in (".infox", ".spe", ".infoanime"):
        el = soup.select_one(sel)
        if el:
            info_text += " " + el.get_text(" ", strip=True)
    if re.search(r"\bManhwa\b", info_text, re.I):
        type_ = "MANHWA"
    elif re.search(r"\bManhua\b", info_text, re.I):
        type_ = "MANHUA"
    elif re.search(r"\bManga\b", info_text, re.I):
        type_ = "MANGA"
    mstat = re.search(r"Status:\s*([A-Za-z]+)", info_text, re.I)
    if mstat:
        status = mstat.group(1).strip()

    # genre
    genres: List[str] = []
    for a in soup.select(".genre-info a, .mgen a, a[href*='/genres/']"):
        g = a.get_text(strip=True)
        if g and g not in genres and not g.lower().startswith("komik "):
            genres.append(g)

    # chapters: utamakan #chapter_list (urutan situs = terbaru di atas)
    chapters: List[Dict[str, Any]] = []
    seen_ch = set()

    def _add_chapter(a) -> None:
        href = _abs(a.get("href") or "")
        if not href or href in seen_ch or "-chapter-" not in href:
            return
        seen_ch.add(href)
        num = ""
        ch_tag = a.select_one("chapter")
        if ch_tag:
            num = ch_tag.get_text(strip=True)
        if not num:
            m = CHAPTER_NUM_RE.search(href)
            num = m.group(1) if m else ""
        # judul: selalu "Chapter {num}" jika ada nomor (hindari "Chapter Awal" / tanggal)
        if num:
            judul_ch = f"Chapter {num}"
        else:
            judul_ch = re.sub(r"\s+", " ", a.get_text(" ", strip=True))
            judul_ch = re.sub(
                r"\b(\d+\s*(menit|jam|hari|minggu|bulan|tahun)|kemarin|yang lalu).*$",
                "",
                judul_ch,
                flags=re.I,
            ).strip() or "Chapter"
        ch_slug = href.rstrip("/").split("/")[-1]
        chapters.append(
            {
                "judul": judul_ch,
                "title": judul_ch,
                "slug": ch_slug,
                "number": num,
                "url": href,
            }
        )

    # 1) hanya link di list chapter (span.lchx) — satu link per chapter
    list_links = soup.select("#chapter_list li span.lchx a[href*='-chapter-'], #chapter_list li a[href*='-chapter-']")
    if list_links:
        for a in list_links:
            # di setiap <li> bisa 2 <a> (judul + tanggal); cukup yang di .lchx atau pertama unik by href
            _add_chapter(a)
    else:
        for a in soup.select(".bxcl a[href*='-chapter-'], a[href*='-chapter-']"):
            _add_chapter(a)

    return {
        "judul": judul or slug,
        "title": judul or slug,
        "slug": slug,
        "thumbnail": thumb,
        "sampul": thumb,
        "sinopsis": sinopsis,
        "synopsis": sinopsis,
        "type": _norm_type(type_),
        "status": status,
        "genres": genres,
        "genre": genres,
        "chapters": chapters,
        "source": "bacakomik",
        "url": f"{BASE}/komik/{slug}/",
    }


def parse_chapter_images(html: str) -> List[str]:
    """
    Ambil panel chapter.
    Prioritas: URL yang mengandung /media/.../chapter-...
    Dedup berdasarkan nama file (nomor halaman).
    Filter iklan.
    """
    soup = BeautifulSoup(html, "html.parser")
    candidates: List[str] = []

    # 1) img di area reader
    for sel in ("#readerarea img", "#chimg img", ".rdl img", ".maincontent img", "noscript img", "img"):
        for img in soup.select(sel):
            src = _img_from_tag(img)
            if src:
                candidates.append(src)

    # 2) regex di HTML mentah (mirror CDN)
    for m in re.finditer(
        r"https?://[^\s\"'<>]+/media/\d+/chapter-[^\"'<>\s]+?\.(?:webp|jpg|jpeg|png)",
        html,
        re.I,
    ):
        candidates.append(m.group(0))

    # filter + dedupe
    out: List[str] = []
    seen_key = set()
    for u in candidates:
        u = _clean_img(u)
        if not u or AD_RE.search(u):
            continue
        # hanya panel chapter / upload serius
        low = u.lower()
        if not any(x in low for x in ("/media/", "chapter-", "wp-content/uploads")):
            continue
        # skip logo site
        if "ikon" in low or "logo" in low or "avatar" in low:
            continue
        # key: basename tanpa domain (supaya mirror CDN tidak dobel)
        base = urlparse(u).path.rsplit("/", 1)[-1]
        # normalisasi nomor halaman di nama file
        key = re.sub(r"_(?:\d{3})\.(webp|jpg|jpeg|png)$", r".\1", base, flags=re.I)
        key = base  # pakai basename utuh agar urutan halaman tetap
        if key in seen_key:
            continue
        seen_key.add(key)
        out.append(u)

    # jika masih campur mirror, pilih satu host dominan untuk konsistensi
    if len(out) > 3:
        hosts: Dict[str, int] = {}
        for u in out:
            h = urlparse(u).netloc
            hosts[h] = hosts.get(h, 0) + 1
        # prioritaskan komikcdn / domain media
        preferred = sorted(
            hosts.keys(),
            key=lambda h: (
                0 if "komikcdn" in h else 1 if "/media/" in h else 2,
                -hosts[h],
            ),
        )
        best = preferred[0] if preferred else None
        if best and hosts.get(best, 0) >= max(3, len(out) // 4):
            filtered = [u for u in out if urlparse(u).netloc == best]
            if len(filtered) >= 3:
                out = filtered

    return out


# ── routes ───────────────────────────────────────────────

@app.get("/")
async def root():
    return {
        "name": "Bacakomik API by Nat",
        "version": APP_VERSION,
        "source": BASE,
        "endpoints": {
            "health": "/health",
            "latest": "/api/latest?page=1",
            "popular": "/api/popular?page=1",
            "colored": "/api/colored?page=1",
            "search": "/api/search?q=",
            "genre": "/api/genre/{slug}?page=1",
            "genres": "/api/genres",
            "detail": "/api/detail/{slug}",
            "chapter": "/api/chapter?slug=&chapter=  OR  ?url=",
            "proxy_img": "/api/img?url=",
        },
    }


@app.get("/health")
async def health():
    return {"status": True, "version": APP_VERSION, "base": BASE}


@app.get("/api/latest")
async def api_latest(page: int = Query(1, ge=1)):
    path = "/komik-terbaru/" if page <= 1 else f"/komik-terbaru/page/{page}/"
    html = await fetch_html(path)
    data = parse_list_html(html)
    return {"status": True, "source": "bacakomik", "page": page, "total": len(data), "data": data}


@app.get("/api/popular")
async def api_popular(page: int = Query(1, ge=1)):
    path = "/komik-populer/" if page <= 1 else f"/komik-populer/page/{page}/"
    html = await fetch_html(path)
    data = parse_list_html(html)
    return {"status": True, "source": "bacakomik", "page": page, "total": len(data), "data": data}


@app.get("/api/colored")
async def api_colored(page: int = Query(1, ge=1)):
    path = "/komik-berwarna/" if page <= 1 else f"/komik-berwarna/page/{page}/"
    html = await fetch_html(path)
    data = parse_list_html(html)
    return {"status": True, "source": "bacakomik", "page": page, "total": len(data), "data": data}


@app.get("/api/search")
async def api_search(q: str = Query(..., min_length=1), page: int = Query(1, ge=1)):
    q = q.strip()
    if page <= 1:
        path = f"/?s={quote(q)}"
    else:
        path = f"/page/{page}/?s={quote(q)}"
    html = await fetch_html(path)
    data = parse_list_html(html)
    return {
        "status": True,
        "source": "bacakomik",
        "query": q,
        "page": page,
        "total": len(data),
        "data": data,
    }


@app.get("/api/genres")
async def api_genres():
    html = await fetch_html("/daftar-genre/")
    soup = BeautifulSoup(html, "html.parser")
    genres = []
    seen = set()
    for a in soup.select("a[href*='/genres/']"):
        href = a.get("href") or ""
        m = re.search(r"/genres/([^/]+)/?", href)
        if not m:
            continue
        slug = m.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        name = a.get_text(strip=True) or slug
        genres.append({"slug": slug, "name": name, "url": _abs(href)})
    return {"status": True, "source": "bacakomik", "total": len(genres), "data": genres}


@app.get("/api/genre/{slug}")
async def api_genre(slug: str, page: int = Query(1, ge=1)):
    slug = slug.strip("/")
    path = f"/genres/{slug}/" if page <= 1 else f"/genres/{slug}/page/{page}/"
    html = await fetch_html(path)
    data = parse_list_html(html)
    return {
        "status": True,
        "source": "bacakomik",
        "genre": slug,
        "page": page,
        "total": len(data),
        "data": data,
    }


@app.get("/api/detail/{slug}")
async def api_detail(slug: str):
    slug = slug.strip("/")
    html = await fetch_html(f"/komik/{slug}/")
    detail = parse_detail_html(html, slug)
    if not detail.get("judul") and not detail.get("chapters"):
        raise HTTPException(status_code=404, detail="Komik tidak ditemukan / parse gagal")
    return {"status": True, "source": "bacakomik", "data": detail}


@app.get("/api/chapter")
async def api_chapter(
    slug: str = Query(""),
    chapter: str = Query(""),
    url: str = Query(""),
):
    """
    Ambil gambar chapter.
    - ?url=https://bacakomik.my/nano-machine-chapter-338/
    - ?slug=nano-machine&chapter=338
    - ?slug=nano-machine&chapter=nano-machine-chapter-338
    """
    page_url = (url or "").strip()
    if not page_url:
        s = (slug or "").strip()
        c = (chapter or "").strip()
        if not s or not c:
            raise HTTPException(
                status_code=400,
                detail="Butuh url=... atau slug=...&chapter=...",
            )
        if c.startswith("http"):
            page_url = c
        elif "-chapter-" in c:
            page_url = f"{BASE}/{c.strip('/')}/"
        else:
            # chapter=338 → /nano-machine-chapter-338/
            num = re.sub(r"^[^\d]*", "", c) or c
            page_url = f"{BASE}/{s}-chapter-{num}/"

    html = await fetch_html(page_url, referer=BASE + "/")
    images = parse_chapter_images(html)
    if not images:
        raise HTTPException(status_code=404, detail="Gambar chapter kosong / terkunci")

    # meta
    series_slug = slug or _slug_from_url(page_url)
    ch_num = chapter
    m = CHAPTER_NUM_RE.search(page_url)
    if m:
        ch_num = m.group(1)

    return {
        "status": True,
        "source": "bacakomik",
        "slug": series_slug,
        "chapter": ch_num,
        "url": page_url,
        "total_images": len(images),
        "images": images,
        "gambar": images,
    }


@app.get("/api/img")
async def api_img(url: str = Query(...)):
    """Proxy gambar (Referer bacakomik) untuk CDN yang hotlink-protection."""
    target = _abs(url)
    if not target.startswith("http"):
        raise HTTPException(400, "url tidak valid")
    headers = {
        "User-Agent": BROWSER_HEADERS["User-Agent"],
        "Referer": BASE + "/",
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True) as client:
        res = await client.get(target, headers=headers)
        if res.status_code >= 400:
            raise HTTPException(res.status_code, f"Gagal ambil gambar ({res.status_code})")
        ctype = res.headers.get("content-type") or "image/jpeg"
        return Response(content=res.content, media_type=ctype)


handler = Mangum(app, lifespan="off")
