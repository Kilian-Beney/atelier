"""Moteur de détourage : chargement multi-format + suppression du fond en pleine résolution."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
except Exception:  # HEIC optionnel
    pass

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif", ".avif"}
PDF_EXT = {".pdf"}
SUPPORTED_EXT = IMAGE_EXT | PDF_EXT

MODELS = {
    "BiRefNet – qualité maximale (recommandé)": "birefnet-general",
    "BiRefNet HR – très haute résolution": "birefnet-hrsod",
    "BiRefNet Lite – rapide": "birefnet-general-lite",
    "BiRefNet Portrait – personnes": "birefnet-portrait",
    "ISNet – léger et rapide": "isnet-general-use",
    "ISNet Anime – dessins / illustrations": "isnet-anime",
    "U2Net – classique": "u2net",
}


@dataclass
class Source:
    """Une image à détourer (un fichier image, ou une page de PDF)."""

    path: Path
    page: int | None = None  # index de page pour les PDF

    @property
    def label(self) -> str:
        return f"{self.path.name} (page {self.page + 1})" if self.page is not None else self.path.name

    @property
    def stem(self) -> str:
        return f"{self.path.stem}_p{self.page + 1:03d}" if self.page is not None else self.path.stem


def expand_paths(paths: list[str]) -> list[Source]:
    """Transforme fichiers/dossiers en liste de sources (1 source par page de PDF)."""
    files: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            files += sorted(f for f in p.rglob("*") if f.suffix.lower() in SUPPORTED_EXT)
        elif p.suffix.lower() in SUPPORTED_EXT:
            files.append(p)

    sources: list[Source] = []
    for f in files:
        if f.suffix.lower() in PDF_EXT:
            import pymupdf as fitz

            with fitz.open(f) as doc:
                sources += [Source(f, i) for i in range(doc.page_count)]
        else:
            sources.append(Source(f))
    return sources


def load_image(src: Source, pdf_dpi: int = 300) -> Image.Image:
    """Charge l'image en conservant sa résolution native (PDF rendu au DPI choisi)."""
    if src.page is not None:
        import pymupdf as fitz

        with fitz.open(src.path) as doc:
            page = doc[src.page]
            native = _native_scan(doc, page)
            if native is not None:
                return native
            pix = page.get_pixmap(dpi=pdf_dpi, alpha=False, colorspace=fitz.csRGB)
            img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            img.info["dpi"] = (pdf_dpi, pdf_dpi)
            return img

    img = Image.open(src.path)
    if getattr(img, "n_frames", 1) > 1:
        img.seek(0)
    info = dict(img.info)
    img = ImageOps.exif_transpose(img)  # respecte l'orientation photo

    # Conversion des modes exotiques vers RGB(A) 8 bits sans rééchantillonnage
    if img.mode in ("I;16", "I;16B", "I;16L", "I"):
        img = img.point(lambda v: v / 257).convert("L")
    if img.mode == "CMYK":
        img = img.convert("RGB")
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGBA" if ("transparency" in info or img.mode in ("LA", "PA", "P")) else "RGB")

    for key in ("icc_profile", "dpi"):
        if key in info:
            img.info[key] = info[key]
    return img


def _native_scan(doc, page) -> Image.Image | None:
    """Page PDF = un seul scan pleine page sans texte : on extrait l'image d'origine, pixel pour pixel."""
    import pymupdf as fitz

    images = page.get_images(full=True)
    if len(images) != 1 or page.rotation or page.get_text().strip() or page.get_drawings():
        return None
    xref = images[0][0]
    rects = page.get_image_rects(xref)
    if not rects or rects[0].get_area() < 0.9 * page.rect.get_area():
        return None
    try:
        pix = fitz.Pixmap(doc, xref)
        if pix.alpha or pix.colorspace is None or pix.colorspace.n not in (1, 3):
            pix = fitz.Pixmap(fitz.csRGB, pix)
        img = Image.frombytes("L" if pix.n == 1 else "RGB", (pix.width, pix.height), pix.samples)
    except Exception:
        return None
    dpi = round(pix.width / (rects[0].width / 72))
    img.info["dpi"] = (dpi, dpi)
    return img


_sessions: dict[str, object] = {}


def get_session(model: str):
    if model not in _sessions:
        from rembg import new_session

        _sessions[model] = new_session(model)
    return _sessions[model]


def remove_background(
    img: Image.Image,
    model: str,
    refine_edges: bool = False,
    clean_mask: bool = False,
) -> Image.Image:
    """Calcule le masque puis l'applique sur les pixels ORIGINAUX (aucune recompression, taille identique)."""
    from rembg import remove

    rgb = img.convert("RGB")
    mask = remove(
        rgb,
        session=get_session(model),
        only_mask=True,
        post_process_mask=clean_mask,
    )
    if not isinstance(mask, Image.Image):
        mask = Image.fromarray(mask)
    mask = mask.convert("L")
    if mask.size != rgb.size:
        mask = mask.resize(rgb.size, Image.Resampling.LANCZOS)

    if refine_edges:
        mask = _refine_alpha(rgb, mask)

    # Conserve la transparence déjà présente dans l'original
    if img.mode == "RGBA":
        from PIL import ImageChops

        mask = ImageChops.multiply(mask, img.getchannel("A"))

    out = rgb.copy()
    out.putalpha(mask)
    out.info.update({k: v for k, v in img.info.items() if k in ("icc_profile", "dpi")})
    return out


def _refine_alpha(rgb: Image.Image, mask: Image.Image) -> Image.Image:
    """Affinage des bords (cheveux, fourrure) par alpha matting pymatting."""
    import numpy as np
    from pymatting import estimate_alpha_cf
    from scipy.ndimage import binary_erosion

    m = np.asarray(mask)
    fg = binary_erosion(m > 240, iterations=10)
    bg = binary_erosion(m < 10, iterations=10)
    trimap = np.full(m.shape, 128, dtype=np.uint8)
    trimap[fg] = 255
    trimap[bg] = 0

    # Travail sur une version réduite si l'image est énorme (mémoire), puis remise à l'échelle
    max_side = 2048
    scale = min(1.0, max_side / max(rgb.size))
    if scale < 1.0:
        size = (round(rgb.width * scale), round(rgb.height * scale))
        small_rgb = rgb.resize(size, Image.Resampling.LANCZOS)
        small_tri = Image.fromarray(trimap).resize(size, Image.Resampling.NEAREST)
    else:
        small_rgb, small_tri = rgb, Image.fromarray(trimap)

    alpha = estimate_alpha_cf(
        np.asarray(small_rgb, dtype=np.float64) / 255.0,
        np.asarray(small_tri, dtype=np.float64) / 255.0,
    )
    refined = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8))
    if refined.size != rgb.size:
        refined = refined.resize(rgb.size, Image.Resampling.LANCZOS)

    # On ne remplace l'alpha que dans la zone incertaine du trimap
    r = np.asarray(refined)
    out = np.where(trimap == 128, r, trimap).astype(np.uint8)
    return Image.fromarray(out)


def crop_to_content(img: Image.Image, margin: int = 0) -> Image.Image:
    bbox = img.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if not bbox:
        return img
    l, t, r, b = bbox
    box = (max(0, l - margin), max(0, t - margin), min(img.width, r + margin), min(img.height, b + margin))
    cropped = img.crop(box)
    cropped.info.update(img.info)
    return cropped


FORMATS = {
    "PNG (sans perte)": ".png",
    "WebP (sans perte)": ".webp",
    "TIFF (sans perte)": ".tif",
}


def save_image(img: Image.Image, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    kw: dict = {}
    if img.info.get("icc_profile"):
        kw["icc_profile"] = img.info["icc_profile"]
    if img.info.get("dpi"):
        kw["dpi"] = tuple(float(d) for d in img.info["dpi"])

    ext = dest.suffix.lower()
    tmp = dest.with_name(dest.name + ".part")
    if ext == ".png":
        img.save(tmp, "PNG", compress_level=6, **kw)
    elif ext == ".webp":
        img.save(tmp, "WEBP", lossless=True, quality=100, method=6, exact=True, **kw)
    else:
        img.save(tmp, "TIFF", compression="tiff_adobe_deflate", **kw)
    os.replace(tmp, dest)


def unique_path(p: Path) -> Path:
    if not p.exists():
        return p
    i = 2
    while True:
        cand = p.with_name(f"{p.stem} ({i}){p.suffix}")
        if not cand.exists():
            return cand
        i += 1
