"""Détourage côté Python : liste d'images / pages de PDF, aperçus, traitement par lot."""

from __future__ import annotations

import base64
import io
import threading
import traceback
from pathlib import Path

from PIL import Image

from moteurs import detourage as moteur
from moteurs import separation
from plateforme import ouvrir

TAILLE_APERCU = 900


def _apercu(img: Image.Image | None) -> str | None:
    """Miniature PNG en data URL (la transparence est rendue sur damier par l'interface)."""
    if img is None:
        return None
    mini = img.copy()
    mini.thumbnail((TAILLE_APERCU, TAILLE_APERCU), Image.Resampling.LANCZOS)
    tampon = io.BytesIO()
    mini.convert("RGBA").save(tampon, "PNG", compress_level=1)
    return "data:image/png;base64," + base64.b64encode(tampon.getvalue()).decode()


class Detourage:
    def __init__(self, emettre):
        self._emettre = emettre
        self._sources: list[moteur.Source] = []
        self._arret = threading.Event()
        self._en_cours = False
        self._dernier_dossier: Path | None = None
        self._fenetre = None

    def options(self) -> dict:
        return {"modeles": list(moteur.MODELS), "formats": list(moteur.FORMATS),
                "extensions": sorted(moteur.SUPPORTED_EXT)}

    # ---------- Liste ----------

    def _liste(self, erreur: str | None = None) -> dict:
        return {"sources": [s.label for s in self._sources], "erreur": erreur}

    def ajouter(self, chemins: list[str]) -> dict:
        if self._en_cours:
            return self._liste()
        try:
            nouvelles = moteur.expand_paths(chemins)
        except Exception as e:
            return self._liste(f"Impossible de lire certains fichiers : {e}")
        connues = {(s.path, s.page) for s in self._sources}
        for s in nouvelles:
            if (s.path, s.page) not in connues:
                self._sources.append(s)
                connues.add((s.path, s.page))
        return self._liste()

    def choisir_fichiers(self) -> dict:
        import webview

        motif = ";".join(f"*{e}" for e in sorted(moteur.SUPPORTED_EXT))
        chemins = self._fenetre.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=True,
                                                   file_types=(f"Images et PDF ({motif})",))
        return self.ajouter(list(chemins or []))

    def choisir_dossier(self) -> dict:
        import webview

        return self.ajouter(list(self._fenetre.create_file_dialog(webview.FileDialog.FOLDER) or []))

    def choisir_sortie(self) -> str | None:
        import webview

        choix = self._fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        return choix[0] if choix else None

    def retirer(self, indices: list[int]) -> dict:
        if not self._en_cours:
            for i in sorted(set(indices), reverse=True):
                if 0 <= i < len(self._sources):
                    del self._sources[i]
        return self._liste()

    def vider(self) -> dict:
        if not self._en_cours:
            self._sources.clear()
        return self._liste()

    # ---------- Aperçus ----------

    def apercu(self, index: int) -> dict:
        try:
            src = self._sources[index]
            return {"original": _apercu(moteur.load_image(src, 72 if src.page is not None else 0))}
        except Exception as e:
            return {"erreur": f"Aperçu impossible : {e}"}

    def apercu_decoupage(self, index: int, dpi: int, options: dict) -> dict:
        try:
            img = moteur.load_image(self._sources[index], int(dpi))
            parties = separation.split_elements(img, **options)
            return {"original": _apercu(img), "resultat": _apercu(separation.overview(img, parties)),
                    "nb": len(parties)}
        except Exception as e:
            return {"erreur": f"Aperçu impossible : {e}"}

    # ---------- Traitement ----------

    def lancer(self, o: dict) -> dict:
        if self._en_cours:
            return {"erreur": "Un traitement est déjà en cours."}
        if not self._sources:
            return {"erreur": "Ajoute d'abord des images ou des PDF."}
        self._en_cours = True
        self._arret.clear()
        o = {**o, "model": moteur.MODELS[o["modele"]], "ext": moteur.FORMATS[o["format"]]}
        self._dernier_dossier = Path(o["sortie"]) if o["sortie"] else self._sources[0].path.parent / "detoure"
        threading.Thread(target=self._travailler, args=(list(self._sources), o), daemon=True).start()
        return {"ok": True}

    def arreter(self) -> None:
        self._arret.set()

    def _travailler(self, sources: list[moteur.Source], o: dict) -> None:
        ok = err = 0
        total = len(sources)
        try:
            for i, src in enumerate(sources):
                if self._arret.is_set():
                    break
                try:
                    if i == 0 and o["mode"] == "ia":
                        self._emettre("det_progression", i=i, total=total,
                                      message="Chargement du modèle IA (téléchargement au 1er lancement)…")
                    sortie = Path(o["sortie"]) if o["sortie"] else src.path.parent / "detoure"
                    if o["mode"] == "separer":
                        self._emettre("det_progression", i=i, total=total, message=f"Séparation : {src.label}")
                        img = moteur.load_image(src, o["dpi"])
                        parties = separation.split_elements(img, **o["separation"])
                        dossier = sortie / f"{src.stem}_elements"
                        for n, (element, _) in enumerate(parties, 1):
                            element.info.update({k: v for k, v in img.info.items() if k in ("icc_profile", "dpi")})
                            dest = dossier / f"{src.stem}_element_{n:02d}{o['ext']}"
                            moteur.save_image(element, dest if o["ecraser"] else moteur.unique_path(dest))
                        ok += 1
                        self._emettre("det_element", i=i, ok=True, message=f"{len(parties)} éléments → {dossier.name}",
                                      original=_apercu(img), resultat=_apercu(separation.overview(img, parties)))
                        continue

                    self._emettre("det_progression", i=i, total=total, message=f"Détourage : {src.label}")
                    img = moteur.load_image(src, o["dpi"])
                    out = moteur.remove_background(img, o["model"], o["affiner"], o["nettoyer"])
                    if o["recadrer"]:
                        out = moteur.crop_to_content(out, int(o["marge"]))
                    dest = sortie / f"{src.stem}{o['suffixe']}{o['ext']}"
                    if not o["ecraser"]:
                        dest = moteur.unique_path(dest)
                    moteur.save_image(out, dest)
                    ok += 1
                    self._emettre("det_element", i=i, ok=True, message=f"{dest.name}  ({out.width}×{out.height})",
                                  original=_apercu(img), resultat=_apercu(out))
                except Exception as e:
                    err += 1
                    traceback.print_exc()
                    self._emettre("det_element", i=i, ok=False, message=f"Erreur : {e}")
        finally:
            self._en_cours = False
            self._emettre("det_termine", ok=ok, erreurs=err, arrete=self._arret.is_set())

    def ouvrir_sortie(self) -> None:
        if self._dernier_dossier and self._dernier_dossier.exists():
            ouvrir(self._dernier_dossier)
