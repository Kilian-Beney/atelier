"""Identification des personnes qui parlent, 100 % locale.

Méthode : Whisper donne l'horodatage de chaque mot → découpage en phrases (pauses, ponctuation)
→ une empreinte vocale par phrase (modèle WeSpeaker, via sherpa-onnx) → regroupement des voix.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

RESSOURCES = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
EMPREINTE = RESSOURCES / "modeles" / "wespeaker_en_voxceleb_resnet34_LM.onnx"
TAUX = 16000
PAUSE = 0.3            # silence (s) qui sépare deux phrases
DUREE_FIABLE = 1.5     # une phrase plus courte donne une empreinte trop bruitée pour former un groupe
DUREE_MAX = 10.0       # au-delà, l'empreinte est calculée sur le milieu de la phrase (plus rapide, aussi fiable)
SILHOUETTE_MINIMALE = 0.25  # mode auto : en dessous, les « groupes » de voix ne sont pas nets → une seule voix
PART_MINIMALE = 0.03   # en mode auto, une « voix » qui parle moins de 3 % du temps est rattachée à une autre


@dataclass
class Mot:
    debut: float
    fin: float
    texte: str


@dataclass
class Phrase:
    mots: list[Mot]
    personne: int = 0

    @property
    def debut(self) -> float:
        return self.mots[0].debut

    @property
    def fin(self) -> float:
        return self.mots[-1].fin


@dataclass
class Replique:
    personne: int | None
    debut: float
    fin: float
    mots: list[Mot] = field(default_factory=list)
    correction: str | None = None  # texte corrigé à la main (remplace celui de Whisper)

    @property
    def texte(self) -> str:
        if self.correction is not None:
            return self.correction
        return "".join(m.texte for m in self.mots).strip()


def fusionner(repliques: list[Replique]) -> list[Replique]:
    """Fusionne les répliques consécutives d'une même personne (après un changement de personne)."""
    resultat: list[Replique] = []
    for r in repliques:
        if not r.texte:
            continue
        precedente = resultat[-1] if resultat else None
        if precedente and r.personne is not None and precedente.personne == r.personne:
            if precedente.correction is not None or r.correction is not None:
                precedente.correction = f"{precedente.texte} {r.texte}"
            precedente.mots += r.mots
            precedente.fin = r.fin
        else:
            resultat.append(r)
    return resultat


def decouper_phrases(mots: list[Mot]) -> list[Phrase]:
    phrases: list[Phrase] = []
    courant: list[Mot] = []
    for mot in mots:
        if courant:
            precedent = courant[-1]
            fin_de_phrase = precedent.texte.rstrip().endswith((".", "?", "!", "…"))
            if mot.debut - precedent.fin >= PAUSE or fin_de_phrase or mot.fin - courant[0].debut > 30:
                phrases.append(Phrase(courant))
                courant = []
        courant.append(mot)
    if courant:
        phrases.append(Phrase(courant))
    return phrases


class _Empreintes:
    """Calcule les empreintes vocales en tâche de fond (4 extracteurs en parallèle sur le processeur),
    pendant que la carte graphique continue la transcription."""

    def __init__(self, audio: np.ndarray):
        import threading
        from concurrent.futures import ThreadPoolExecutor

        self.audio = audio
        self.pool = ThreadPoolExecutor(max_workers=4)
        self.local = threading.local()
        self.fils = max(1, (os.cpu_count() or 8) // 8)
        self.futures: list = []

    def _extracteur(self):
        import sherpa_onnx

        if not hasattr(self.local, "ext"):
            self.local.ext = sherpa_onnx.SpeakerEmbeddingExtractor(sherpa_onnx.SpeakerEmbeddingExtractorConfig(
                model=str(EMPREINTE), num_threads=self.fils))
        return self.local.ext

    def _calculer(self, debut: float, fin: float) -> np.ndarray | None:
        if fin - debut > DUREE_MAX:
            milieu = (debut + fin) / 2
            debut, fin = milieu - DUREE_MAX / 2, milieu + DUREE_MAX / 2
        if fin - debut < 0.6:  # trop court pour le modèle : on élargit un peu la fenêtre
            milieu = (debut + fin) / 2
            debut, fin = milieu - 0.3, milieu + 0.3
        ext = self._extracteur()
        flux = ext.create_stream()
        flux.accept_waveform(TAUX, self.audio[max(0, int(debut * TAUX)): int(fin * TAUX)])
        flux.input_finished()
        if not ext.is_ready(flux):
            return None
        v = np.array(ext.compute(flux), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    def ajouter(self, phrases: list[Phrase]) -> None:
        for p in phrases[len(self.futures):]:
            self.futures.append(self.pool.submit(self._calculer, p.debut, p.fin))

    def resultats(self, progression: Callable[[float], None] | None) -> np.ndarray:
        vecteurs: list[np.ndarray | None] = []
        for i, f in enumerate(self.futures):
            vecteurs.append(f.result())
            if progression and i % 10 == 0:
                progression(i / len(self.futures))
        self.pool.shutdown()
        dim = next((v.shape[0] for v in vecteurs if v is not None), 256)
        return np.stack([v if v is not None else np.zeros(dim, dtype=np.float32) for v in vecteurs])


def _centroides(vecteurs: np.ndarray, groupes: np.ndarray, masque: np.ndarray, n: int) -> np.ndarray:
    c = np.zeros((n, vecteurs.shape[1]), dtype=np.float32)
    for k in range(n):
        choisis = vecteurs[(groupes == k) & masque]
        if len(choisis) == 0:
            choisis = vecteurs[groupes == k]
        if len(choisis):
            m = choisis.mean(axis=0)
            c[k] = m / (np.linalg.norm(m) + 1e-9)
    return c


def _detecter_nombre(base: np.ndarray) -> np.ndarray:
    """Essaie 2 à 8 voix et garde le découpage le plus net (silhouette) ; une seule voix si aucun ne l'est."""
    from sklearn.cluster import AgglomerativeClustering
    from sklearn.metrics import silhouette_score

    meilleur, meilleurs_groupes = SILHOUETTE_MINIMALE, np.zeros(len(base), dtype=int)
    for k in range(2, min(8, len(base) - 1) + 1):
        groupes = AgglomerativeClustering(n_clusters=k, metric="cosine", linkage="average").fit_predict(base)
        score = silhouette_score(base, groupes, metric="cosine")
        if score > meilleur:
            meilleur, meilleurs_groupes = score, groupes
    return meilleurs_groupes


def _regrouper(vecteurs: np.ndarray, durees: np.ndarray, nb: int | None) -> np.ndarray:
    from sklearn.cluster import AgglomerativeClustering

    fiables = durees >= DUREE_FIABLE
    if fiables.sum() < max(2, nb or 2):
        fiables = np.ones_like(fiables)
    base = vecteurs[fiables]
    if nb == 1 or len(base) < 2:
        return np.zeros(len(vecteurs), dtype=int)
    if nb:
        groupes_base = AgglomerativeClustering(
            n_clusters=min(nb, len(base)), metric="cosine", linkage="average").fit_predict(base)
    else:
        groupes_base = _detecter_nombre(base)
    n = int(groupes_base.max()) + 1

    groupes = np.zeros(len(vecteurs), dtype=int)
    groupes[fiables] = groupes_base
    centres = _centroides(vecteurs, groupes, fiables, n)
    # Affinage : chaque phrase (y compris les courtes) rejoint la voix la plus proche, puis on recalcule.
    for _ in range(3):
        groupes = np.argmax(vecteurs @ centres.T, axis=1)
        centres = _centroides(vecteurs, groupes, fiables, n)

    if not nb:  # une « voix » quasi muette est en général un artefact : on la fond dans la plus proche
        total = durees.sum() or 1
        while n > 1:
            parts = np.array([durees[groupes == k].sum() / total for k in range(n)])
            petite = int(np.argmin(parts))
            if parts[petite] >= PART_MINIMALE:
                break
            centres = np.delete(centres, petite, axis=0)
            n -= 1
            groupes = np.argmax(vecteurs @ centres.T, axis=1)
    return groupes


class Identificateur:
    """À alimenter au fil de la transcription (ajouter), puis terminer() une fois tous les mots connus."""

    def __init__(self, audio: np.ndarray):
        self.mots: list[Mot] = []
        self.empreintes = _Empreintes(audio)

    def ajouter(self, mots: list[Mot]) -> None:
        self.mots += mots
        phrases = decouper_phrases(self.mots)
        self.empreintes.ajouter(phrases[:-1])  # la dernière phrase peut encore s'allonger

    def terminer(self, nb_personnes: int | None,
                 progression: Callable[[float], None] | None = None) -> list[Replique]:
        """nb_personnes : None = détection automatique."""
        phrases = decouper_phrases(self.mots)
        if not phrases:
            return []
        self.empreintes.ajouter(phrases)
        vecteurs = self.empreintes.resultats(progression)
        durees = np.array([p.fin - p.debut for p in phrases])
        groupes = _regrouper(vecteurs, durees, nb_personnes)

        # Personne 1 = la première qui parle, Personne 2 = la suivante, etc.
        ordre: dict[int, int] = {}
        for p, g in zip(phrases, groupes):
            p.personne = ordre.setdefault(int(g), len(ordre))

        repliques: list[Replique] = []
        for p in phrases:
            if repliques and repliques[-1].personne == p.personne:
                repliques[-1].mots += p.mots
                repliques[-1].fin = p.fin
            else:
                repliques.append(Replique(p.personne, p.debut, p.fin, list(p.mots)))
        return [r for r in repliques if r.texte]


def paragraphes(mots: list[Mot]) -> list[Replique]:
    """Sans identification : paragraphes coupés aux pauses marquées, pour un texte lisible."""
    blocs: list[Replique] = []
    for p in decouper_phrases(mots):
        if blocs and p.debut - blocs[-1].fin < 1.5 and blocs[-1].fin - blocs[-1].debut < 60:
            blocs[-1].mots += p.mots
            blocs[-1].fin = p.fin
        else:
            blocs.append(Replique(None, p.debut, p.fin, list(p.mots)))
    return [b for b in blocs if b.texte]
