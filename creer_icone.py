"""Génère ui/icone.png, icone.ico (Windows) et icone.icns (macOS) (dégradé violet → rose, onde sonore et étincelle)."""

import numpy as np
from PIL import Image, ImageDraw

T = 1024
HAUTEURS = [0.26, 0.5, 0.78, 0.56, 0.36]


def main() -> None:
    y, x = np.mgrid[0:T, 0:T]
    t = ((x + y) / (2 * T))[..., None]
    a, b = np.array([124, 92, 255]), np.array([236, 72, 153])
    degrade = Image.fromarray((a + (b - a) * t).astype(np.uint8), "RGB").convert("RGBA")
    masque = Image.new("L", (T, T), 0)
    ImageDraw.Draw(masque).rounded_rectangle((0, 0, T - 1, T - 1), radius=230, fill=255)
    icone = Image.new("RGBA", (T, T), (0, 0, 0, 0))
    icone.paste(degrade, mask=masque)

    dessin = ImageDraw.Draw(icone)
    largeur, ecart, x0 = 76, 46, 170
    for h in HAUTEURS:
        hauteur = int(h * T * 0.62)
        y0 = (T - hauteur) // 2 + 40
        dessin.rounded_rectangle((x0, y0, x0 + largeur, y0 + hauteur), radius=largeur // 2, fill="white")
        x0 += largeur + ecart
    # Étincelle à quatre branches en haut à droite
    cx, cy, r, c = 780, 250, 150, 34
    dessin.polygon([(cx, cy - r), (cx + c, cy - c), (cx + r, cy), (cx + c, cy + c), (cx, cy + r),
                    (cx - c, cy + c), (cx - r, cy), (cx - c, cy - c)], fill="white")

    icone.resize((256, 256), Image.LANCZOS).save("ui/icone.png")
    icone.save("icone.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    icone.save("icone.icns")  # macOS


if __name__ == "__main__":
    main()
