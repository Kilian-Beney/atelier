// Transcription : liste de fichiers, texte en direct, noms des personnes, corrections, réécoute.
const COULEURS = ["#A594FF", "#3DDC97", "#FFB547", "#5BC0EB", "#FF7AA2", "#C3E88D", "#F78C6C", "#89DDFF"];
const AIDES_PERSONNES = {
  "1": "Cours, conférence, note vocale : pas de distinction des voix.",
  "0": "Moins fiable : indique plutôt le nombre si tu le connais.",
};
const AIDE_TEXTE = "Clic sur un nom : écouter · Double-clic : corriger · Ctrl+Espace : pause";  // même raccourci sur Mac
const TR = {
  fichiers: [],               // { chemin, nom, etat, statut, termine }
  resultats: new Map(),       // chemin → résultat (ordre d'affichage)
  enCours: false,
  fichierNoms: null,
  direct: null,               // bloc du texte en cours de transcription
  lecture: null,
  audioCharge: false,
  surlignee: null,
  dernierDossier: null,
  qualite: "Équilibré",
};
const couleur = (p) => COULEURS[p % COULEURS.length];

// ---------- Liste de fichiers ----------
function trListe() {
  const liste = $("tr-liste");
  liste.innerHTML = "";
  if (!TR.fichiers.length) { liste.innerHTML = '<div class="vide">Aucun fichier pour l\'instant</div>'; return; }
  TR.fichiers.forEach((f) => {
    const l = document.createElement("div");
    l.className = "ligne-fichier " + (f.etat || "");
    l.innerHTML = `<span class="pastille">●</span><span class="nom"></span><button class="suppr" title="Retirer">✕</button><span class="statut"></span>`;
    l.querySelector(".nom").textContent = f.nom;
    l.querySelector(".nom").title = f.chemin;
    l.querySelector(".statut").textContent = f.statut || "En attente";
    l.querySelector(".suppr").addEventListener("click", () => {
      if (TR.enCours) return;
      TR.fichiers = TR.fichiers.filter((x) => x !== f);
      trListe();
    });
    f.el = l;
    liste.appendChild(l);
  });
}
function trStatutFichier(chemin, etat, texte) {
  const f = TR.fichiers.find((x) => x.chemin === chemin);
  if (!f) return;
  f.etat = etat; f.statut = texte;
  if (f.el) {
    f.el.className = "ligne-fichier " + etat;
    f.el.querySelector(".statut").textContent = texte;
  }
}
function trAjouter(r) {
  const deja = new Set(TR.fichiers.map((f) => f.chemin));
  let ajoutes = 0;
  for (const f of r.fichiers) {
    if (deja.has(f.chemin)) continue;
    deja.add(f.chemin);
    TR.fichiers.push({ ...f, etat: "", statut: "En attente", termine: false });
    ajoutes++;
  }
  trListe();
  if (ajoutes || r.ignores) {
    let msg = `${ajoutes} fichier(s) ajouté(s).`;
    if (r.ignores) msg += ` ${r.ignores} ignoré(s) (format non audio).`;
    statut("tr", msg);
  }
  return ajoutes;
}
receveurs.transcription = async (chemins) => {
  trOnglet("fichiers");
  trAjouter(await api.transcription.developper(chemins));
};
$("tr-depot").addEventListener("click", async () => trAjouter(await api.transcription.choisir_fichiers()));
$("tr-ajout-dossier").addEventListener("click", async () => trAjouter(await api.transcription.choisir_dossier()));
$("tr-vider").addEventListener("click", () => { if (!TR.enCours) { TR.fichiers = []; trListe(); } });

// ---------- Onglets Fichiers / Dictaphone ----------
const choisirOnglet = segmente("tr-onglets", null, (v) => trOnglet(v));
function trOnglet(v) {
  choisirOnglet(v);
  $("tr-fichiers").hidden = v !== "fichiers";
  $("tr-dictaphone").hidden = v !== "dictaphone";
  R.tr_onglet = v;
  sauverReglages();
  document.dispatchEvent(new CustomEvent("onglet-transcription", { detail: v }));
}

// ---------- Réglages ----------
const choisirQualite = segmente("tr-qualite", null, (v) => { TR.qualite = v; R.tr_qualite = v; sauverReglages(); trVerifierModele(); });
async function trVerifierModele() {
  const q = TR.qualite;
  const r = await api.transcription.modele_installe(q);
  if (q !== TR.qualite) return;
  const info = $("tr-info-modele");
  if (r.installe) { info.textContent = "✓ Installé sur ce PC · fonctionne hors ligne"; info.className = "aide ok"; }
  else { info.textContent = `↓ Téléchargement unique de ${r.taille} (internet requis)`; info.className = "aide attention"; }
}
function trAidePersonnes() {
  const v = $("tr-personnes").value;
  const aide = $("tr-aide-personnes");
  aide.textContent = AIDES_PERSONNES[v] || "Chaque voix sera identifiée, puis tu pourras la nommer.";
  aide.className = "aide" + (v === "0" ? " attention" : "");
}
$("tr-langue").addEventListener("change", () => { R.tr_langue = $("tr-langue").value; sauverReglages(); });
$("tr-personnes").addEventListener("change", () => { R.tr_personnes = $("tr-personnes").value; trAidePersonnes(); sauverReglages(); });
$("tr-ameliorer").addEventListener("change", () => { R.tr_ameliorer = $("tr-ameliorer").checked; sauverReglages(); });

// ---------- Lancement ----------
function trVerrouiller(v) {
  $("tr-qualite").classList.toggle("verrou", v);
  $("tr-qualite").querySelectorAll("button").forEach((b) => (b.disabled = v));
  for (const id of ["tr-langue", "tr-personnes", "tr-ameliorer", "tr-vider", "tr-appliquer-noms"]) $(id).disabled = v;
}
$("tr-lancer").addEventListener("click", () => {
  if (TR.enCours) {
    api.transcription.arreter();
    $("tr-lancer").textContent = "Arrêt en cours…";
    $("tr-lancer").disabled = true;
    return;
  }
  trLancer();
});
function trLancer(seulement = null) {
  if (TR.enCours) return false;
  if (!TR.fichiers.length) { statut("tr", "Ajoute d'abord au moins un fichier audio (ou enregistre avec le dictaphone).", "attente"); return false; }
  let aFaire = seulement ? TR.fichiers.filter((f) => seulement.includes(f.chemin)) : TR.fichiers.filter((f) => !f.termine);
  if (!aFaire.length) aFaire = [...TR.fichiers];
  for (const f of aFaire) {
    f.termine = false;
    trStatutFichier(f.chemin, "", "En attente");
    TR.resultats.delete(f.chemin);
  }
  TR.enCours = true;
  TR.direct = null;
  trRendu();
  $("tr-lancer").textContent = "Arrêter";
  $("tr-lancer").classList.add("arret");
  trVerrouiller(true);
  progression("tr", 0);
  api.transcription.lancer(aFaire.map((f) => f.chemin), TR.qualite, $("tr-langue").value,
    +$("tr-personnes").value, $("tr-ameliorer").checked);
  return true;
}
// Utilisé par le dictaphone : ajoute un fichier et le transcrit tout de suite si possible.
window.transcrireFichier = (chemin, nom) => {
  trAjouter({ fichiers: [{ chemin, nom }], ignores: 0 });
  if (TR.enCours) { toast("Une transcription est en cours : l'enregistrement a été ajouté à la liste."); return; }
  trLancer([chemin]);
};

// ---------- Événements venus de Python ----------
// ---------- Matériel : carte NVIDIA (Windows), puce Apple (Mac) ou processeur ----------
sur("tr_materiel", (e) => {
  if (e.acceleration === "cuda") badge("tr-badge", "⚡ Accélération GPU NVIDIA", "ok");
  else if (e.acceleration === "mlx") badge("tr-badge", "⚡ Puce Apple (Metal)", "ok");
  else badge("tr-badge", "Mode processeur (CPU)", "attente");
  if (!TR.gpuEnCours) $("tr-gpu-installer").hidden = !(e.acceleration === "cpu" && e.nvidia);
});
$("tr-gpu-installer").addEventListener("click", async () => {
  if (TR.gpuEnCours) { api.transcription.annuler_gpu(); return; }
  if (!confirm("Télécharger l'accélération NVIDIA (environ 1,4 Go, une seule fois) ?\n\n"
    + "Les transcriptions seront ensuite 5 à 10 fois plus rapides.")) return;
  const r = await api.transcription.installer_gpu();
  if (r.erreur) { statut("tr", r.erreur, "attente"); return; }
  TR.gpuEnCours = true;
  $("tr-lancer").disabled = true;
  $("tr-gpu-installer").textContent = "Annuler le téléchargement";
  statut("tr", "Téléchargement de l'accélération NVIDIA…");
  progression("tr", 0);
});
sur("tr_gpu_progression", (e) => { statut("tr", `Téléchargement de l'accélération NVIDIA : ${e.texte}`); progression("tr", e.progression); });
sur("tr_gpu_fin", (e) => {
  TR.gpuEnCours = false;
  $("tr-lancer").disabled = false;
  $("tr-gpu-installer").textContent = "⚡ Activer le GPU NVIDIA";
  if (e.ok) { $("tr-gpu-installer").hidden = true; statut("tr", "✓ Accélération NVIDIA installée : les prochaines transcriptions utiliseront la carte graphique.", "ok"); progression("tr", 1); }
  else { statut("tr", e.message, "attente"); progression("tr", 0); }
});
sur("tr_chargement", (e) => {
  statut("tr", e.telecharge
    ? `Téléchargement du modèle « ${TR.qualite} », une seule fois… Ça peut prendre quelques minutes.`
    : `Chargement du modèle « ${TR.qualite} »…`);
  progression("tr", null);
});
sur("tr_debut", (e) => {
  progression("tr", e.i / e.n);
  trStatutFichier(e.chemin, "encours", "Transcription en cours…");
  statut("tr", `Fichier ${e.i + 1}/${e.n} : ${e.chemin.split(/[\\/]/).pop()}`);
  const zone = $("tr-texte");
  if (!TR.resultats.size) zone.innerHTML = "";
  const bloc = document.createElement("div");
  bloc.className = "direct";
  bloc.innerHTML = `<h3></h3>`;
  bloc.firstChild.textContent = e.chemin.split(/[\\/]/).pop();
  zone.appendChild(bloc);
  TR.direct = bloc;
});
sur("tr_segment", (e) => {
  if (!TR.direct) return;
  const zone = $("tr-texte");
  const enBas = zone.scrollTop + zone.clientHeight >= zone.scrollHeight - 60;
  const p = document.createElement("p");
  p.innerHTML = `<span class="t">${hms(e.debut)}</span>`;
  p.appendChild(document.createTextNode(e.texte));
  TR.direct.appendChild(p);
  if (enBas) zone.scrollTop = zone.scrollHeight;
  progression("tr", e.progression);
});
sur("tr_etape", (e) => {
  trStatutFichier(e.chemin, "encours", e.texte);
  statut("tr", `${e.chemin.split(/[\\/]/).pop()} : ${e.texte}`);
  progression("tr", e.progression);
});
sur("tr_fin_fichier", (e) => {
  const res = e.resultat;
  const f = TR.fichiers.find((x) => x.chemin === e.chemin);
  if (f) f.termine = true;
  const voix = res.avec_personnes ? `  ·  ${res.personnes.length} voix` : "";
  trStatutFichier(e.chemin, "ok", `✓ Terminé  ·  ${court(res.duree)} d'audio${voix}`);
  TR.resultats.set(e.chemin, res);
  TR.direct = null;
  TR.dernierDossier = e.chemin;
  trRendu();
  $("tr-texte").scrollTop = $("tr-texte").scrollHeight;
  if (res.avec_personnes) trPreparerNoms(e.chemin);
  progression("tr", e.progression);
});
sur("tr_erreur_fichier", (e) => trStatutFichier(e.chemin, "ko", "✕ Erreur : " + raccourcir(e.message, 60)));
sur("tr_arrete", (e) => trStatutFichier(e.chemin, "stop", "Arrêté"));
sur("tr_erreur", (e) => statut("tr", "Erreur : " + e.message, "ko"));
sur("tr_termine", (e) => {
  TR.enCours = false;
  TR.direct = null;
  $("tr-lancer").textContent = "Lancer la transcription";
  $("tr-lancer").classList.remove("arret");
  $("tr-lancer").disabled = false;
  trVerrouiller(false);
  trRendu();
  if (e.arrete) statut("tr", `Arrêté. ${e.reussis}/${e.total} fichier(s) transcrit(s).`, "attente");
  else if (e.reussis === e.total) {
    progression("tr", 1);
    const fin = !$("tr-noms").hidden ? " Nomme chaque personne ci-dessus." : "";
    statut("tr", `✓ ${e.reussis} fichier(s) transcrit(s), enregistré(s) à côté de l'audio (.txt et .srt).${fin}`, "ok");
  } else if (!$("tr-statut").classList.contains("ko")) {
    statut("tr", `${e.reussis}/${e.total} fichier(s) transcrit(s), certains ont échoué.`, "attente");
  }
});

// ---------- Affichage du texte ----------
function trRendu() {
  const zone = $("tr-texte");
  const direct = TR.direct;
  zone.innerHTML = "";
  for (const [chemin, res] of TR.resultats) {
    const titre = document.createElement("h3");
    titre.textContent = res.nom;
    zone.appendChild(titre);
    res.repliques.forEach((r, k) => {
      const d = document.createElement("div");
      d.className = "replique";
      d.dataset.chemin = chemin;
      d.dataset.index = k;
      const qui = r.personne !== null
        ? `<b style="color:${couleur(r.personne)}">${echapper(res.noms[r.personne])}</b>` : "";
      d.innerHTML = `<span class="entete">${qui}<span class="t">${hms(r.debut)}</span><span class="jouer">▶</span></span><p></p>`;
      d.querySelector("p").textContent = r.texte;
      zone.appendChild(d);
    });
  }
  if (direct) zone.appendChild(direct);
  if (!zone.childElementCount) zone.innerHTML = '<span class="placeholder">Le texte apparaîtra ici au fur et à mesure de la transcription…</span>';
  const aTexte = TR.resultats.size > 0;
  $("tr-copier").disabled = $("tr-vers-mep").disabled = $("tr-gdocs").disabled = !aTexte;
  $("tr-dossier").disabled = !TR.dernierDossier;
  $("lec-aide").textContent = aTexte ? AIDE_TEXTE : "";
  TR.surlignee = null;
}
$("tr-texte").addEventListener("click", (e) => {
  const entete = e.target.closest(".entete");
  if (!entete) return;
  const d = entete.closest(".replique");
  trJouer(d.dataset.chemin, +d.dataset.index);
});
$("tr-texte").addEventListener("dblclick", (e) => {
  const d = e.target.closest(".replique");
  if (!d) return;
  if (TR.enCours) { statut("tr", "Attends la fin de la transcription pour corriger le texte.", "attente"); return; }
  window.getSelection().removeAllRanges();
  ouvrirEditeur(d.dataset.chemin, +d.dataset.index);
});
$("tr-copier").addEventListener("click", async () => {
  const texte = await api.transcription.texte([...TR.resultats.keys()]);
  if (await copier(texte)) statut("tr", "✓ Texte copié dans le presse-papiers.", "ok");
});
// Couleurs plus foncées que celles de l'écran : lisibles sur une page blanche.
const COULEURS_DOC = ["#6A4FE0", "#1F9D6B", "#C77700", "#1E7FB8", "#D6336C", "#5C8A1E", "#C65A2E", "#0F7FA8"];
function trHtml() {
  let html = "";
  for (const res of TR.resultats.values()) {
    html += `<h2>${echapper(res.nom.replace(/\.[^.]+$/, ""))}</h2>`;
    for (const r of res.repliques) {
      const temps = `<span style="color:#888888;font-size:9pt">[${hms(r.debut)}]</span>`;
      const qui = r.personne !== null
        ? `<b style="color:${COULEURS_DOC[r.personne % COULEURS_DOC.length]}">${echapper(res.noms[r.personne])}</b> ${temps}<br>`
        : `${temps} `;
      html += `<p>${qui}${echapper(r.texte)}</p>`;
    }
  }
  return html;
}
$("tr-gdocs").addEventListener("click", () => {
  if (versGoogleDocs(trHtml())) statut("tr", `✓ Texte copié : colle-le (${coller()}) dans le Google Docs qui vient de s'ouvrir.`, "ok");
});
$("tr-dossier").addEventListener("click", () => TR.dernierDossier && api.transcription.ouvrir_dossier(TR.dernierDossier));
$("tr-vers-mep").addEventListener("click", async () => {
  const texte = await api.transcription.texte([...TR.resultats.keys()]);
  window.mepRemplir(texte, [...TR.resultats.values()].map((r) => r.nom).join(", "));
  outil("mise-en-page");
});

// ---------- Noms des personnes ----------
function trPreparerNoms(chemin) {
  const res = TR.resultats.get(chemin);
  if (!res) return;
  TR.fichierNoms = chemin;
  const champs = $("tr-champs-noms");
  champs.innerHTML = "";
  for (const p of res.personnes) {
    const premiere = res.repliques.find((r) => r.personne === p);
    const c = document.createElement("div");
    c.className = "case";
    c.innerHTML = `<i style="color:${couleur(p)}">●</i><input type="text" placeholder="Personne ${p + 1}"><small></small>`;
    const input = c.querySelector("input");
    input.value = res.noms_saisis[p] || "";
    input.dataset.personne = p;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") trAppliquerNoms(); });
    c.querySelector("small").textContent = premiere ? `« ${raccourcir(premiere.texte, 38)} »` : "";
    champs.appendChild(c);
  }
  const avecVoix = [...TR.resultats.values()].filter((r) => r.avec_personnes);
  const choix = $("tr-noms-fichier");
  choix.hidden = avecVoix.length < 2;
  choix.innerHTML = avecVoix.map((r) => `<option value="${echapper(r.chemin)}">${echapper(r.nom)}</option>`).join("");
  choix.value = chemin;
  $("tr-noms").hidden = false;
}
$("tr-noms-fichier").addEventListener("change", () => trPreparerNoms($("tr-noms-fichier").value));
async function trAppliquerNoms() {
  if (TR.enCours || !TR.fichierNoms) return;
  const noms = {};
  $("tr-champs-noms").querySelectorAll("input").forEach((i) => { noms[i.dataset.personne] = i.value; });
  const r = await api.transcription.nommer(TR.fichierNoms, noms);
  if (r.erreur) { statut("tr", r.erreur, "ko"); return; }
  TR.resultats.set(TR.fichierNoms, r.resultat);
  trRendu();
  const nom = r.resultat.nom.replace(/\.[^.]+$/, "");
  statut("tr", `✓ Noms appliqués dans le texte, ${nom}.txt et ${nom}.srt.`, "ok");
}
$("tr-appliquer-noms").addEventListener("click", trAppliquerNoms);

// ---------- Fenêtre de correction ----------
let edition = null;
function ouvrirEditeur(chemin, index) {
  const res = TR.resultats.get(chemin);
  const r = res.repliques[index];
  edition = { chemin, index };
  const qui = $("ed-qui");
  const avecPersonne = r.personne !== null;
  qui.hidden = $("ed-qui-label").hidden = !avecPersonne;
  if (avecPersonne) {
    qui.innerHTML = res.personnes.map((p) => `<option value="${p}">${echapper(res.noms[p])}</option>`).join("")
      + `<option value="nouvelle">+ Nouvelle personne</option>`;
    qui.value = r.personne;
  }
  $("ed-temps").textContent = `${hms(r.debut)} → ${hms(r.fin)}`;
  $("ed-texte").value = r.texte;
  $("editeur").hidden = false;
  setTimeout(() => $("ed-texte").focus(), 50);
}
function fermerEditeur() { $("editeur").hidden = true; edition = null; }
async function enregistrerEdition() {
  if (!edition) return;
  const { chemin, index } = edition;
  const res = TR.resultats.get(chemin);
  let personne = null;
  if (!$("ed-qui").hidden) {
    personne = $("ed-qui").value === "nouvelle" ? Math.max(-1, ...res.personnes) + 1 : +$("ed-qui").value;
  }
  const texte = $("ed-texte").value;
  fermerEditeur();
  const r = await api.transcription.corriger(chemin, index, texte, personne);
  if (r.erreur) { statut("tr", r.erreur, "ko"); return; }
  TR.resultats.set(chemin, r.resultat);
  trRendu();
  if (r.resultat.avec_personnes) trPreparerNoms(chemin);
  const nom = r.resultat.nom.replace(/\.[^.]+$/, "");
  statut("tr", `✓ Correction enregistrée dans ${nom}.txt et ${nom}.srt.`, "ok");
}
$("ed-annuler").addEventListener("click", fermerEditeur);
$("ed-enregistrer").addEventListener("click", enregistrerEdition);
$("ed-ecouter").addEventListener("click", () => edition && trJouer(edition.chemin, edition.index));
$("editeur").addEventListener("keydown", (e) => {
  if (e.key === "Escape") fermerEditeur();
  if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); enregistrerEdition(); }
});
$("editeur").addEventListener("mousedown", (e) => { if (e.target === $("editeur")) fermerEditeur(); });

// ---------- Réécoute ----------
function trJouer(chemin, index) {
  const r = TR.resultats.get(chemin).repliques[index];
  api.transcription.jouer(chemin, Math.max(r.debut - 0.3, 0));
}
sur("lecture_chargement", (e) => { $("lec-nom").textContent = `Chargement de l'audio de ${e.nom}…`; });
sur("lecture_prete", (e) => { $("lec-nom").textContent = e.nom; TR.audioCharge = true; TR.surlignee = null; });
sur("lecture_erreur", (e) => { $("lec-nom").textContent = "Lecture impossible"; statut("tr", "Impossible de lire l'audio : " + e.message, "ko"); });
function trLecturePause() {
  if (!TR.audioCharge) {
    const [chemin, res] = [...TR.resultats][0] || [];
    if (res && res.repliques.length) trJouer(chemin, 0);
    return;
  }
  api.transcription.lecture_pause();
}
$("lec-lecture").addEventListener("click", trLecturePause);
$("lec-moins").addEventListener("click", () => api.transcription.decaler(-5));
$("lec-plus").addEventListener("click", () => api.transcription.decaler(5));
document.addEventListener("keydown", (e) => {
  if (e.ctrlKey && e.code === "Space" && outilActif === "transcription" && $("editeur").hidden) {
    e.preventDefault();
    trLecturePause();
  }
});
setInterval(async () => {
  if (!TR.audioCharge || !api) return;
  const l = await api.transcription.etat_lecture();
  if (!l) return;
  $("lec-temps").textContent = `${hms(l.position)} / ${hms(l.duree)}`;
  $("lec-lecture").textContent = l.en_lecture ? "⏸" : "▶";
  trSurligner(l);
}, 200);
function trSurligner(l) {
  const res = TR.resultats.get(l.chemin);
  if (!res) return;
  const k = res.repliques.findIndex((r) => r.debut - 0.3 <= l.position && l.position < r.fin + 0.3);
  const cle = `${l.chemin}|${k}`;
  if (k < 0 || cle === TR.surlignee) return;
  TR.surlignee = cle;
  document.querySelectorAll(".replique.lecture").forEach((d) => d.classList.remove("lecture"));
  const d = [...document.querySelectorAll(".replique")].find((x) => x.dataset.chemin === l.chemin && +x.dataset.index === k);
  if (d) {
    d.classList.add("lecture");
    if (l.en_lecture) d.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }
}

demarrage(async () => {
  if (R.tr_qualite) { TR.qualite = R.tr_qualite; choisirQualite(TR.qualite); }
  if (R.tr_langue) $("tr-langue").value = R.tr_langue;
  $("tr-personnes").value = R.tr_personnes ?? "2";
  $("tr-ameliorer").checked = !!R.tr_ameliorer;
  trAidePersonnes();
  trOnglet(R.tr_onglet || "fichiers");
  trVerifierModele();
  api.transcription.demarrer(TR.qualite);
});
