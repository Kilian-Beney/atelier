"""Atelier : Transcription (+ dictaphone), Mise en page et Détourage réunis dans une seule application.
Tout tourne en local : rien ne quitte le PC."""

import json
import os
import queue
import sys
import threading
import time
from pathlib import Path

# En mode fenêtré (exe PyInstaller), stdout/stderr valent None et font planter les barres de téléchargement.
from plateforme import MAC, dossier_donnees, dossier_journal, ouvrir  # noqa: E402

if sys.stdout is None or sys.stderr is None or getattr(sys, "frozen", False):
    _journal = dossier_journal()
    _journal.mkdir(parents=True, exist_ok=True)
    _log = open(_journal / "atelier.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = _log if getattr(sys, "frozen", False) else (sys.stdout or _log)
    sys.stderr = _log if getattr(sys, "frozen", False) else (sys.stderr or _log)

import webview
from webview.dom import DOMEventHandler

from api_detourage import Detourage
from api_mise_en_page import MiseEnPage
from api_transcription import Transcription
from dictaphone import DOSSIER_DEFAUT, Dictaphone

RESSOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
REGLAGES = dossier_donnees() / "reglages.json"


class Emetteur:
    """Envoie les événements Python → interface par paquets (un seul appel JS toutes les 40 ms au plus),
    depuis un fil dédié : les calculs ne sont jamais bloqués par l'affichage."""

    def __init__(self):
        self.fenetre = None
        self.file: queue.Queue = queue.Queue()
        threading.Thread(target=self._boucle, daemon=True).start()

    def __call__(self, type_: str, **donnees) -> None:
        self.file.put({"type": type_, **donnees})

    def _boucle(self) -> None:
        while True:
            paquet = [self.file.get()]
            time.sleep(0.04)
            while not self.file.empty():
                paquet.append(self.file.get_nowait())
            if self.fenetre is None:
                continue
            try:
                self.fenetre.run_js(f"window.recevoir({json.dumps(paquet, ensure_ascii=False, default=str)})")
            except Exception:
                pass


class Api:
    def __init__(self, emetteur: Emetteur):
        self._emetteur = emetteur
        self.transcription = Transcription(emetteur)
        self.dictaphone = Dictaphone(emetteur)
        self.mise_en_page = MiseEnPage(emetteur)
        self.detourage = Detourage(emetteur)
        self._a_ouvrir: list[str] = []

    def _relier(self, fenetre) -> None:
        self._emetteur.fenetre = fenetre
        for outil in (self.transcription, self.mise_en_page, self.detourage):
            outil._fenetre = fenetre

    def reglages(self) -> dict:
        try:
            r = json.loads(REGLAGES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            r = {}
        r.setdefault("dic_dossier", str(DOSSIER_DEFAUT))
        return r

    def enregistrer_reglages(self, r: dict) -> None:
        try:
            REGLAGES.parent.mkdir(parents=True, exist_ok=True)
            REGLAGES.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass

    def choisir_dossier(self) -> str | None:
        choix = self.transcription._fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        return choix[0] if choix else None

    def ouvrir_dossier(self, chemin: str) -> None:
        p = Path(chemin)
        if not p.exists() and not p.suffix:
            p.mkdir(parents=True, exist_ok=True)
        ouvrir(p if p.is_dir() else p.parent)

    def ouvrir_google_docs(self) -> None:
        """Ouvre un nouveau document Google Docs dans le navigateur (le texte est déjà dans le presse-papiers)."""
        import webbrowser

        webbrowser.open("https://docs.new")

    def systeme(self) -> dict:
        return {"mac": MAC}

    def fichiers_de_depart(self) -> list[str]:
        """Fichiers passés en argument (ex. glissés sur l'icône de l'exe)."""
        a_ouvrir, self._a_ouvrir = self._a_ouvrir, []
        return a_ouvrir


def verifier(sortie: str, audio: str | None = None) -> None:
    """Vérification de l'application compilée : chaque moteur se charge-t-il ? Avec un fichier audio,
    fait aussi une vraie transcription (modèle « Rapide »). Usage : Atelier --verifier resultat.txt [audio]"""
    lignes = []
    tests = [
        ("materiel", "from moteurs.transcrire import materiel; r = materiel()"),
        ("whisper", "import faster_whisper, ctranslate2; r = ctranslate2.__version__"),
        ("voix", "import sherpa_onnx, sklearn.cluster; from moteurs.personnes import EMPREINTE; r = EMPREINTE.exists()"),
        ("audio", "import av, sounddevice, scipy.signal; from moteurs.audio import ameliorer; import numpy as np; "
                  "r = len(ameliorer(np.zeros(16000, dtype=np.float32), 16000))"),
        ("micro", "from dictaphone import Dictaphone; r = len(Dictaphone(lambda *a, **k: None).peripheriques()['liste'])"),
        ("detourage", "import rembg, onnxruntime, pymatting, pymupdf, pillow_heif; r = onnxruntime.__version__"),
        ("mise_en_page", "import markdown; from moteurs.mise_en_page import en_html, trouver_navigateur; "
                         "r = (en_html('# ok'), trouver_navigateur())"),
    ]
    if MAC:
        tests.insert(1, ("mlx", "import mlx.core as mx, mlx_whisper; r = mx.default_device()"))
    tests.append(("fenetre", "import webview\nfrom webview.platforms import "
                             + ("cocoa" if MAC else "winforms") + "\nr = 'ok'"))
    tests.append(("detourage_reel", """
from PIL import Image, ImageDraw
from moteurs import detourage, separation
img = Image.new("RGB", (480, 360), "white")
ImageDraw.Draw(img).ellipse((140, 80, 340, 280), fill=(200, 40, 60))
out = detourage.remove_background(img, "isnet-general-use")
alpha = out.getchannel("A")
r = (out.size, alpha.getpixel((5, 5)), alpha.getpixel((240, 180)), len(separation.split_elements(img)))"""))
    if audio:
        tests.append(("transcription", f"""
from pathlib import Path
from moteurs.transcrire import charger_modele, transcrire_audio
modele, device = charger_modele("small", "auto")
res = transcrire_audio(modele, Path({audio!r}), None, 2, device=device)
r = (device, round(res.duree, 1), [x.texte for x in res.repliques])"""))
    for nom, code in tests:
        try:
            espace: dict = {}
            exec(code, espace)
            lignes.append(f"OK    {nom}: {espace.get('r')}")
        except Exception as e:
            lignes.append(f"ECHEC {nom}: {e!r}")
    Path(sortie).write_text("\n".join(lignes), encoding="utf-8")


def main() -> None:
    if len(sys.argv) in (3, 4) and sys.argv[1] == "--verifier":
        verifier(*sys.argv[2:])
        os._exit(0)
    emetteur = Emetteur()
    api = Api(emetteur)
    # Fichiers passés en argument (Windows : glissés sur l'icône ; macOS ignore -psn_… ajouté par le Finder).
    api._a_ouvrir = [a for a in sys.argv[1:] if not a.startswith(("--", "-psn"))]
    ecran = webview.screens[0] if webview.screens else None
    largeur = min(1320, ecran.width - 60) if ecran else 1320
    hauteur = min(860, ecran.height - 100) if ecran else 860
    fenetre = webview.create_window(
        "Atelier", str(RESSOURCES / "ui" / "index.html"), js_api=api, width=largeur, height=hauteur,
        min_size=(1040, 640), background_color="#0E1016")
    api._relier(fenetre)

    def depot(e) -> None:
        fichiers = e.get("dataTransfer", {}).get("files", [])
        chemins = [f["pywebviewFullPath"] for f in fichiers if f.get("pywebviewFullPath")]
        if chemins:
            emetteur("depot", chemins=chemins)

    def charge() -> None:
        fenetre.dom.document.events.drop += DOMEventHandler(depot, prevent_default=True, stop_propagation=True)

    def fermeture() -> None:
        # Un enregistrement en cours est finalisé proprement (rien n'est perdu).
        api.dictaphone.fermer()
        api.transcription.couper_son()

    fenetre.events.loaded += charge
    fenetre.events.closing += fermeture
    webview.start()
    # Sortie immédiate : la libération de CUDA peut bloquer indéfiniment la fermeture sous Windows.
    os._exit(0)


if __name__ == "__main__":
    main()
