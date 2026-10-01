"""Mise en page côté Python : notes → Markdown (IA locale Ollama) → HTML → PDF."""

from __future__ import annotations

import re
import sys
import threading
import time
from pathlib import Path

from moteurs import mise_en_page as moteur
from plateforme import ouvrir

UI = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "ui"


def nom_de_fichier(titre: str) -> str:
    propre = re.sub(r'[<>:"/\\|?*\n\r\t]+', " ", titre).strip()
    return (propre[:80] or "Notes") + ".pdf"


class MiseEnPage:
    def __init__(self, emettre):
        self._emettre = emettre
        self._arret = threading.Event()
        self._ollama_lance = False
        self._fenetre = None

    def etat(self) -> dict:
        try:
            if not self._ollama_lance:
                moteur.demarrer_ollama()
                self._ollama_lance = True
            if not moteur.modele_present():
                if not getattr(self, "_telechargement", None):
                    self._telechargement = {"message": "Téléchargement du modèle d'IA (4,7 Go, une seule fois)…"}
                    threading.Thread(target=self._telecharger_modele, daemon=True).start()
                return {"pret": False, "bloquant": False, "message": self._telechargement["message"]}
            return {"pret": True}
        except moteur.OllamaIndisponible as e:
            return {"pret": False, "bloquant": True, "message": str(e)}
        except Exception:
            return {"pret": False, "bloquant": False, "message": "Démarrage de l'IA…"}

    def _telecharger_modele(self) -> None:
        """Premier lancement : Ollama télécharge le modèle d'IA ; la progression s'affiche dans le badge."""
        try:
            for etape in moteur.telecharger_modele():
                total, fait = etape.get("total"), etape.get("completed")
                if total and fait:
                    self._telechargement["message"] = f"Téléchargement du modèle d'IA : {fait / total:.0%}"
        except Exception as e:
            self._telechargement["message"] = f"Échec du téléchargement du modèle ({e}) : nouvel essai…"
        time.sleep(5)
        self._telechargement = None  # si le modèle manque encore, etat() relance le téléchargement

    def ouvrir_fichier(self) -> dict | None:
        import webview

        chemins = self._fenetre.create_file_dialog(
            webview.FileDialog.OPEN, file_types=("Texte (*.txt;*.md)", "Tous les fichiers (*.*)"))
        if not chemins:
            return None
        return self.lire(chemins[0])

    def lire(self, chemin: str) -> dict | None:
        p = Path(chemin)
        for encodage in ("utf-8-sig", "cp1252"):
            try:
                return {"nom": p.name, "texte": p.read_text(encoding=encodage)}
            except UnicodeDecodeError:
                continue
            except OSError:
                return None
        return None

    def arreter(self) -> None:
        self._arret.set()

    def lancer(self, notes: str, mode: str) -> dict:
        self._arret.clear()
        debut = time.time()
        md = ""
        dernier_envoi = 0.0
        bloc_actuel = -1
        md_bloc = ""
        try:
            blocs = moteur.decouper(notes)
            mots_blocs = [len(b.split()) for b in blocs]
            for morceau, bloc, nb in moteur.generer(notes, mode, self._arret.is_set):
                if bloc != bloc_actuel:
                    bloc_actuel, md_bloc = bloc, ""
                md += morceau
                md_bloc += morceau
                if time.time() - dernier_envoi > 0.15:
                    dernier_envoi = time.time()
                    self._envoyer(md, bloc, nb, len(md_bloc.split()), mots_blocs[bloc])
            self._envoyer(md, max(bloc_actuel, 0), max(len(blocs), 1), 1, 1)
        except moteur.OllamaIndisponible as e:
            return {"erreur": f"l'IA ne répond pas ({e})."}
        except Exception as e:
            return {"erreur": str(e)}
        if self._arret.is_set():
            return {"arrete": True}
        return {"duree": round(time.time() - debut)}

    def _envoyer(self, md: str, bloc: int, nb: int, mots_ecrits: int, mots_bloc: int) -> None:
        self._emettre("mep_page", html=moteur.en_html(md), bloc=bloc, nb=nb, mots_ecrits=mots_ecrits,
                      mots_bloc=mots_bloc)

    def exporter_pdf(self, contenu: str, theme: str, titre: str) -> dict | None:
        import webview

        choix = self._fenetre.create_file_dialog(
            webview.FileDialog.SAVE, save_filename=nom_de_fichier(titre), file_types=("PDF (*.pdf)",))
        if not choix:
            return None
        destination = choix if isinstance(choix, str) else choix[0]
        if not destination.lower().endswith(".pdf"):
            destination += ".pdf"
        css = (UI / "page.css").read_text(encoding="utf-8")
        page = (f'<!DOCTYPE html><html lang="fr"><head><meta charset="utf-8"><title>{titre}</title>'
                f"<style>{css} body{{margin:0}}</style></head>"
                f'<body><article class="page theme-{theme}">{contenu}</article></body></html>')
        try:
            moteur.exporter_pdf(page, destination)
        except Exception as e:
            return {"erreur": str(e)}
        ouvrir(destination)
        return {"chemin": destination}
