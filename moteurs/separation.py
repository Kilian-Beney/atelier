"""Séparation d'un dessin / scan sur fond clair en éléments individuels détourés."""
from __future__ import annotations

import colorsys

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from scipy import ndimage as ndi


def split_elements(
    img: Image.Image,
    ink_threshold: int = 190,  # canal le plus sombre en dessous = dessin
    group_distance: int = 14,  # px : traits plus proches que ça = même élément
    min_area: int = 400,  # px de dessin : en dessous = poussière de scan ignorée
    fill_shapes: bool = True,  # garde l'intérieur des formes fermées opaque (ballon, cadre…)
    min_hole: int = 2500,  # px : un intérieur plus petit reste transparent (trous entre hachures)
    border: int = 12,  # px ignorés au bord de page (ombres de scan)
    padding: int = 10,
) -> list[tuple[Image.Image, tuple[int, int, int, int]]]:
    """Retourne [(élément RGBA, (x0, y0, x1, y1))] dans l'ordre de lecture."""
    rgb = np.asarray(img.convert("RGB"), dtype=np.uint8)
    h, w = rgb.shape[:2]
    darkest = rgb.min(axis=2)  # une couleur vive (jaune, rouge) compte comme du dessin

    ink = darkest < ink_threshold
    if border:
        ink[:border, :] = ink[-border:, :] = False
        ink[:, :border] = ink[:, -border:] = False

    # Regroupe les traits voisins en éléments
    near = ndi.binary_dilation(ink, iterations=group_distance) if group_distance > 0 else ink
    labels, _ = ndi.label(near)
    objects = ndi.find_objects(labels)

    # Couleur du papier et alpha anti-aliasé (0 = papier, 255 = encre pleine)
    paper = np.percentile(rgb.reshape(-1, 3), 90, axis=0).astype(np.float32)
    paper_level = float(paper.min())
    alpha_full = np.clip((paper_level - darkest.astype(np.float32)) / max(1.0, paper_level - 60) * 255, 0, 255)
    if border:  # la bande de bord (ombre du scanner) ne doit jamais apparaître dans un élément
        alpha_full[:border, :] = alpha_full[-border:, :] = 0
        alpha_full[:, :border] = alpha_full[:, -border:] = 0

    results = []
    for idx, sl in enumerate(objects, start=1):
        if sl is None:
            continue
        ys, xs = sl
        y0, y1 = max(0, ys.start - padding), min(h, ys.stop + padding)
        x0, x1 = max(0, xs.start - padding), min(w, xs.stop + padding)

        region = labels[y0:y1, x0:x1] == idx
        el_ink = ink[y0:y1, x0:x1] & region
        if el_ink.sum() < min_area:
            continue

        src = rgb[y0:y1, x0:x1].astype(np.float32)
        a = np.where(region, alpha_full[y0:y1, x0:x1], 0.0)

        # « Dé-mélange » du papier : couleur réelle du trait sous le bord semi-transparent (pas de halo)
        af = np.maximum(a / 255.0, 1e-3)[..., None]
        color = np.clip((src - (1 - af) * paper) / af, 0, 255)

        if fill_shapes:
            # Seuls les VRAIS intérieurs (ballon, cadre…) deviennent opaques ; les petits trous de papier
            # entre les coups de feutre restent transparents, sinon ils font des points blancs.
            closed = ndi.binary_closing(el_ink, iterations=3)
            holes = ndi.binary_fill_holes(closed) & ~closed
            hole_labels, n_holes = ndi.label(holes)
            if n_holes:
                sizes = ndi.sum_labels(holes, hole_labels, index=np.arange(1, n_holes + 1))
                big = np.concatenate([[False], sizes >= min_hole])
                holes = big[hole_labels]
            inside = ndi.binary_dilation(holes, iterations=3) & ~ndi.binary_dilation(el_ink, iterations=1)
            color = np.where(inside[..., None], src, color)  # intérieur : pixels d'origine
            a = np.where(inside, 255.0, a)

        rgba = np.dstack([color, a]).round().astype(np.uint8)
        results.append((Image.fromarray(rgba, "RGBA"), (x0, y0, x1, y1)))

    # Ordre de lecture : bandes horizontales haut → bas, puis gauche → droite
    band = max(1, h // 6)
    results.sort(key=lambda r: (((r[1][1] + r[1][3]) // 2) // band, r[1][0]))
    return results


def overview(img: Image.Image, parts: list[tuple[Image.Image, tuple[int, int, int, int]]], max_side: int = 1600) -> Image.Image:
    """Image d'ensemble avec un cadre numéroté par élément détecté."""
    scale = min(1.0, max_side / max(img.size))
    view = img.convert("RGB").resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    d = ImageDraw.Draw(view)
    try:
        font = ImageFont.truetype("segoeuib.ttf", max(14, round(28 * scale * 2)))
    except OSError:
        font = ImageFont.load_default()
    for i, (_, (x0, y0, x1, y1)) in enumerate(parts, 1):
        r, g, b = colorsys.hsv_to_rgb((i * 0.618) % 1, 0.85, 0.95)
        col = (int(r * 255), int(g * 255), int(b * 255))
        box = [round(x0 * scale), round(y0 * scale), round(x1 * scale), round(y1 * scale)]
        d.rectangle(box, outline=col, width=3)
        label = str(i)
        tb = d.textbbox((box[0], box[1]), label, font=font)
        d.rectangle((tb[0] - 3, tb[1] - 2, tb[2] + 3, tb[3] + 2), fill=col)
        d.text((box[0], box[1]), label, fill="white", font=font)
    return view
