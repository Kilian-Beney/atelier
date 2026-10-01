"""Transcription côté Python : modèles Whisper, file de fichiers, corrections, noms, réécoute."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import numpy as np

from moteurs.personnes import fusionner
from moteurs.transcrire import (EXTENSIONS, Resultat, charger_modele, enregistrer, materiel, modele_installe,
                                nom_personne, texte_final, transcrire_audio)
from plateforme import ouvrir

MODELES = {"Rapide": "small", "Équilibré": "large-v3-turbo", "Précis": "large-v3"}
TAILLES = {"Rapide": "0,5 Go", "Équilibré": "1,6 Go", "Précis": "3 Go"}
LANGUES = {"Français": "fr", "Anglais": "en", "Espagnol": "es", "Allemand": "de",
           "Italien": "it", "Portugais": "pt", "Arabe": "ar", "Détection auto": None}
TAUX_LECTURE = 16000


class _Lecteur:
    """Lecture de l'audio d'un fichier (un seul à la fois en mémoire) à partir d'une position donnée."""

    def __init__(self):
        self.chemin: Path | None = None
        self.audio: np.ndarray | None = None
        self.position = 0.0
        self.depart: float | None = None

    @property
    def duree(self) -> float:
        return len(self.audio) / TAUX_LECTURE if self.audio is not None else 0.0

    def charger(self, chemin: Path) -> None:
        from faster_whisper import decode_audio

        audio = decode_audio(str(chemin), sampling_rate=TAUX_LECTURE)
        self.arreter()
        self.audio = (np.clip(audio, -1, 1) * 32767).astype(np.int16)
        self.chemin = chemin
        self.position = 0.0

    def jouer(self, depuis: float | None = None) -> None:
        import sounddevice as sd

        if self.audio is None:
            return
        sd.stop()
        if depuis is not None:
            self.position = min(max(depuis, 0.0), self.duree)
        if self.position >= self.duree - 0.05:
            self.position = 0.0
        sd.play(self.audio[int(self.position * TAUX_LECTURE):], TAUX_LECTURE)
        self.depart = time.monotonic()

    def pause(self) -> None:
        import sounddevice as sd

        if self.depart is not None:
            self.position = self.actuelle()
            self.depart = None
            sd.stop()

    def arreter(self) -> None:
        self.pause()

    def actuelle(self) -> float:
        if self.depart is None:
            return self.position
        return min(self.position + time.monotonic() - self.depart, self.duree)

    @property
    def en_lecture(self) -> bool:
        return self.depart is not None and self.actuelle() < self.duree


def _serialiser(res: Resultat, noms: dict[int, str]) -> dict:
    return {
        "chemin": str(res.fichier), "nom": res.fichier.name, "duree": res.duree,
        "avec_personnes": res.avec_personnes, "personnes": res.personnes,
        "noms": {str(p): nom_personne(p, noms) for p in res.personnes},
        "noms_saisis": {str(p): n for p, n in noms.items()},
        "repliques": [{"personne": r.personne, "debut": r.debut, "fin": r.fin, "texte": r.texte}
                      for r in res.repliques],
    }


class Transcription:
    def __init__(self, emettre):
        self._emettre = emettre
        self._resultats: dict[Path, Resultat] = {}
        self._noms: dict[Path, dict[int, str]] = {}
        self._lecteur = _Lecteur()
        self._modele = None
        self._nom_modele: str | None = None
        self._device = "cuda"
        self._verrou = threading.Lock()
        self._arret = threading.Event()
        self._en_cours = False
        self._fenetre = None

    # ---------- Démarrage ----------

    def demarrer(self, qualite: str) -> None:
        """Détecte l'accélération disponible puis précharge le modèle (1re transcription sans attente)."""
        def travail() -> None:
            try:
                m = materiel()
            except Exception:
                m = {"acceleration": "cpu", "nvidia": False}
            self._emettre("tr_materiel", **m)
            nom = MODELES.get(qualite, "large-v3-turbo")
            if m["acceleration"] != "cpu" and modele_installe(nom):
                try:
                    self._obtenir_modele(nom)
                except Exception:
                    pass

        threading.Thread(target=travail, daemon=True).start()

    def installer_gpu(self) -> dict:
        """Télécharge l'accélération NVIDIA (Windows, ~1,4 Go, une seule fois)."""
        from moteurs import cuda

        if self._en_cours:
            return {"erreur": "Attends la fin de la transcription en cours."}
        self._arret_gpu = threading.Event()

        def travail() -> None:
            try:
                ok = cuda.telecharger(lambda x, texte: self._emettre("tr_gpu_progression", progression=x, texte=texte),
                                      self._arret_gpu.is_set)
                if not ok:
                    self._emettre("tr_gpu_fin", ok=False, message="Téléchargement annulé.")
                    return
                cuda.ajouter_dll()
                with self._verrou:
                    self._modele, self._nom_modele = None, None  # rechargé sur la carte graphique
                self._emettre("tr_gpu_fin", ok=True)
                self._emettre("tr_materiel", **materiel())
            except Exception as e:
                self._emettre("tr_gpu_fin", ok=False, message=f"Échec du téléchargement : {e}")

        threading.Thread(target=travail, daemon=True).start()
        return {"ok": True}

    def annuler_gpu(self) -> None:
        if getattr(self, "_arret_gpu", None):
            self._arret_gpu.set()

    def modele_installe(self, qualite: str) -> dict:
        return {"installe": modele_installe(MODELES[qualite]), "taille": TAILLES[qualite]}

    # ---------- Fichiers ----------

    def developper(self, chemins: list[str]) -> dict:
        """Fichiers et dossiers déposés → fichiers audio (les autres sont ignorés)."""
        fichiers, ignores = [], 0
        for c in chemins:
            p = Path(c)
            candidats = sorted(p.iterdir()) if p.is_dir() else [p]
            for f in candidats:
                if f.is_file() and f.suffix.lower() in EXTENSIONS:
                    fichiers.append({"chemin": str(f), "nom": f.name})
                elif not p.is_dir():
                    ignores += 1
        return {"fichiers": fichiers, "ignores": ignores}

    def choisir_fichiers(self) -> dict:
        import webview

        motif = ";".join(f"*{e}" for e in sorted(EXTENSIONS))
        chemins = self._fenetre.create_file_dialog(
            webview.FileDialog.OPEN, allow_multiple=True,
            file_types=(f"Fichiers audio ({motif})", "Tous les fichiers (*.*)"))
        return self.developper(list(chemins or []))

    def choisir_dossier(self) -> dict:
        import webview

        dossier = self._fenetre.create_file_dialog(webview.FileDialog.FOLDER)
        return self.developper(list(dossier or []))

    def ouvrir_dossier(self, chemin: str) -> None:
        p = Path(chemin)
        ouvrir(p if p.is_dir() else p.parent)

    # ---------- Transcription ----------

    def lancer(self, chemins: list[str], qualite: str, langue: str, nb_personnes: int, ameliorer: bool) -> dict:
        if self._en_cours:
            return {"erreur": "Une transcription est déjà en cours."}
        self._en_cours = True
        self._arret.clear()
        fichiers = [Path(c) for c in chemins]
        for f in fichiers:
            self._resultats.pop(f, None)
            if self._lecteur.chemin == f:
                self._lecteur.arreter()
        threading.Thread(target=self._travailler,
                         args=(fichiers, MODELES[qualite], LANGUES.get(langue, "fr"), int(nb_personnes),
                               bool(ameliorer)), daemon=True).start()
        return {"ok": True}

    def arreter(self) -> None:
        self._arret.set()

    def _obtenir_modele(self, nom: str):
        with self._verrou:
            if self._modele is None or self._nom_modele != nom:
                self._modele = None
                self._modele, self._device = charger_modele(nom, "auto")
                self._nom_modele = nom
                if self._device == "cpu":
                    self._emettre("tr_materiel", acceleration="cpu", nvidia=materiel()["nvidia"])
            return self._modele

    def _travailler(self, fichiers: list[Path], nom_modele: str, langue: str | None, nb_personnes: int,
                    ameliorer: bool) -> None:
        reussis = 0
        n = len(fichiers)
        try:
            if self._nom_modele != nom_modele:
                self._emettre("tr_chargement", telecharge=not modele_installe(nom_modele))
            modele = self._obtenir_modele(nom_modele)
            for i, f in enumerate(fichiers):
                if self._arret.is_set():
                    break
                c = str(f)
                self._emettre("tr_debut", chemin=c, i=i, n=n)
                try:
                    res = transcrire_audio(
                        modele, f, langue, nb_personnes, device=self._device, arret=self._arret.is_set,
                        ameliorer=ameliorer,
                        sur_segment=lambda t, texte, a, c=c, i=i: self._emettre(
                            "tr_segment", chemin=c, debut=t, texte=texte, progression=(i + a * 0.9) / n),
                        sur_etape=lambda texte, x, c=c, i=i: self._emettre(
                            "tr_etape", chemin=c, texte=texte, progression=(i + 0.9 + 0.1 * (x or 0)) / n),
                    )
                    if res is None:
                        self._emettre("tr_arrete", chemin=c)
                        break
                    self._resultats[f] = res
                    enregistrer(res, self._noms.get(f))
                    reussis += 1
                    self._emettre("tr_fin_fichier", chemin=c, resultat=_serialiser(res, self._noms.get(f, {})),
                                  progression=(i + 1) / n)
                except Exception as e:
                    self._emettre("tr_erreur_fichier", chemin=c, message=str(e))
        except Exception as e:
            self._emettre("tr_erreur", message=str(e))
        finally:
            self._en_cours = False
            self._emettre("tr_termine", reussis=reussis, total=n, arrete=self._arret.is_set())

    # ---------- Corrections et noms ----------

    def corriger(self, chemin: str, index: int, texte: str, personne: int | None) -> dict:
        f = Path(chemin)
        res = self._resultats[f]
        r = res.repliques[index]
        texte = " ".join(texte.split())
        if texte != r.texte:
            r.correction = texte
        if personne is not None:
            r.personne = int(personne)
        res.repliques = fusionner(res.repliques)
        return self._sauver(f)

    def nommer(self, chemin: str, noms: dict) -> dict:
        f = Path(chemin)
        self._noms[f] = {int(p): n.strip() for p, n in noms.items() if n and n.strip()}
        return self._sauver(f)

    def _sauver(self, f: Path) -> dict:
        try:
            enregistrer(self._resultats[f], self._noms.get(f))
        except OSError as e:
            return {"erreur": f"Erreur à l'enregistrement : {e}"}
        return {"resultat": _serialiser(self._resultats[f], self._noms.get(f, {}))}

    def texte(self, chemins: list[str]) -> str:
        textes = {Path(c): texte_final(self._resultats[Path(c)], self._noms.get(Path(c)))
                  for c in chemins if Path(c) in self._resultats}
        if len(textes) == 1:
            return next(iter(textes.values()))
        return "\n\n".join(f"{p.name}\n\n{t}" for p, t in textes.items())

    # ---------- Réécoute ----------

    def jouer(self, chemin: str, depuis: float) -> None:
        f = Path(chemin)
        if self._lecteur.chemin == f:
            self._lecteur.jouer(max(depuis, 0))
            return
        self._lecteur.arreter()
        self._emettre("lecture_chargement", nom=f.name)

        def charger() -> None:
            try:
                self._lecteur.charger(f)
                self._lecteur.jouer(max(depuis, 0))
                self._emettre("lecture_prete", chemin=chemin, nom=f.name)
            except Exception as e:
                self._emettre("lecture_erreur", message=str(e))

        threading.Thread(target=charger, daemon=True).start()

    def lecture_pause(self) -> None:
        if self._lecteur.audio is None:
            return
        if self._lecteur.en_lecture:
            self._lecteur.pause()
        else:
            self._lecteur.jouer()

    def decaler(self, secondes: float) -> None:
        lecteur = self._lecteur
        if lecteur.audio is None:
            return
        position = lecteur.actuelle() + secondes
        if lecteur.en_lecture:
            lecteur.jouer(position)
        else:
            lecteur.position = min(max(position, 0.0), lecteur.duree)

    def etat_lecture(self) -> dict | None:
        lecteur = self._lecteur
        if lecteur.audio is None:
            return None
        if lecteur.depart is not None and not lecteur.en_lecture:
            lecteur.pause()
        return {"chemin": str(lecteur.chemin), "position": lecteur.actuelle(), "duree": lecteur.duree,
                "en_lecture": lecteur.en_lecture}

    def couper_son(self) -> None:
        self._lecteur.arreter()
