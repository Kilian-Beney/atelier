// Dictaphone : choix du micro, sensibilité, gain automatique, réduction de bruit, vumètre et forme d'onde.
const PROFILS = {
  proche:    { sens: 0,  agc: true, bruit: true, force: 40, coupe: true, personnes: "1" },
  entretien: { sens: 6,  agc: true, bruit: true, force: 50, coupe: true, personnes: "2" },
  reunion:   { sens: 12, agc: true, bruit: true, force: 60, coupe: true },
  amphi:     { sens: 18, agc: true, bruit: true, force: 75, coupe: true, personnes: "1" },
};
const DIC = { etat: "arret", micros: [], onde: [], crete: -90, creteT: 0, dernier: null, veutEcouter: false };

// ---------- Réglages du son ----------
function dicLire() {
  return { sens: +$("dic-sens").value, agc: $("dic-agc").checked, bruit: $("dic-bruit").checked,
           force: +$("dic-force").value, coupe: $("dic-coupe-bas").checked };
}
function dicAfficher(r) {
  $("dic-sens").value = r.sens; $("dic-agc").checked = r.agc; $("dic-bruit").checked = r.bruit;
  $("dic-force").value = r.force; $("dic-coupe-bas").checked = r.coupe;
  dicMaj();
}
function dicMaj() {
  const r = dicLire();
  $("dic-sens-val").textContent = `+${r.sens} dB`;
  $("dic-force-val").textContent = r.force < 35 ? "Légère" : r.force < 70 ? "Moyenne" : "Forte";
  $("dic-force").disabled = !r.bruit;
  [$("dic-sens"), $("dic-force")].forEach(remplirCurseur);
  api && api.dictaphone.regler(r.sens, r.agc, r.bruit, r.force / 100, r.coupe);
}
function dicProfil(nom) {
  document.querySelectorAll("#dic-profils button").forEach((b) => b.classList.toggle("actif", b.dataset.profil === nom));
}
function dicSauver() {
  R.dic_son = { ...dicLire(), profil: document.querySelector("#dic-profils .actif")?.dataset.profil || null };
  sauverReglages();
}
for (const id of ["dic-sens", "dic-force"]) $(id).addEventListener("input", () => { dicMaj(); dicProfil(null); dicSauver(); });
for (const id of ["dic-agc", "dic-bruit", "dic-coupe-bas"]) $(id).addEventListener("change", () => { dicMaj(); dicProfil(null); dicSauver(); });
$("dic-profils").addEventListener("click", (e) => {
  const b = e.target.closest("button");
  if (!b) return;
  const p = PROFILS[b.dataset.profil];
  dicAfficher(p);
  dicProfil(b.dataset.profil);
  if (p.personnes && !TR.enCours) {
    $("tr-personnes").value = p.personnes;
    $("tr-personnes").dispatchEvent(new Event("change"));
  }
  dicSauver();
});
$("dic-reinit").addEventListener("click", () => { dicAfficher(PROFILS.entretien); dicProfil("entretien"); dicSauver(); });

// ---------- Micro ----------
async function dicMicros() {
  const r = await api.dictaphone.peripheriques();
  DIC.micros = r.liste;
  const choix = $("dic-micro");
  if (!r.liste.length) {
    choix.innerHTML = "<option>Aucun micro détecté</option>";
    $("dic-etat-micro").textContent = r.erreur ? "Erreur : " + r.erreur : "Branche un micro puis clique sur ⟳";
    return;
  }
  choix.innerHTML = r.liste.map((m) => `<option value="${m.id}">${echapper(m.nom)}${m.id === r.defaut ? " (par défaut)" : ""}</option>`).join("");
  const memorise = r.liste.find((m) => m.nom === R.dic_micro);
  choix.value = memorise ? memorise.id : (r.defaut ?? r.liste[0].id);
}
function dicMicroChoisi() {
  const v = $("dic-micro").value;
  return DIC.micros.some((m) => String(m.id) === v) ? +v : null;
}
$("dic-micro").addEventListener("change", async () => {
  R.dic_micro = DIC.micros.find((m) => String(m.id) === $("dic-micro").value)?.nom;
  sauverReglages();
  if (DIC.etat === "ecoute") await dicEcouter(true);
});
$("dic-actualiser").addEventListener("click", async () => { await dicMicros(); if (DIC.etat === "ecoute") dicEcouter(true); });

async function dicEcouter(forcer = false) {
  if (!forcer && DIC.etat !== "arret") return;
  const r = await api.dictaphone.ecouter(dicMicroChoisi());
  if (r.erreur) { $("dic-etat-micro").textContent = r.erreur; toast(r.erreur, "ko"); return; }
  DIC.veutEcouter = true;
}
function dicCouper() {
  if (DIC.etat === "enregistrement" || DIC.etat === "pause") return;
  DIC.veutEcouter = false;
  api.dictaphone.couper();
  setTimeout(() => dicEtat("arret", 0), 150);
}
$("dic-tester").addEventListener("click", () => (DIC.etat === "ecoute" ? dicCouper() : dicEcouter()));

// Micro ouvert automatiquement quand l'onglet Dictaphone est visible (vumètre en direct).
function dicVisible() { return outilActif === "transcription" && !$("tr-dictaphone").hidden; }
function dicSuivreVisibilite() {
  if (!api) return;
  if (dicVisible()) dicEcouter();
  else if (DIC.etat === "ecoute") dicCouper();
}
document.addEventListener("outil", dicSuivreVisibilite);
document.addEventListener("onglet-transcription", dicSuivreVisibilite);

// ---------- Enregistrement ----------
$("dic-rec").addEventListener("click", dicBasculer);
async function dicBasculer() {
  if (DIC.etat === "enregistrement" || DIC.etat === "pause") return dicArreter();
  $("dic-rec").disabled = true;
  const r = await api.dictaphone.demarrer(dicMicroChoisi(), R.dic_dossier, $("dic-format").value);
  $("dic-rec").disabled = false;
  if (r.erreur) { toast(r.erreur, "ko"); $("dic-etat").textContent = r.erreur; return; }
  DIC.onde = [];
  dicEtat("enregistrement", 0);
}
async function dicArreter() {
  $("dic-rec").disabled = true;
  $("dic-etat").textContent = $("dic-format").value === "wav" ? "Enregistrement du fichier…" : "Conversion du fichier…";
  const r = await api.dictaphone.arreter(dicVisible());
  $("dic-rec").disabled = false;
  dicEtat(dicVisible() ? "ecoute" : "arret", 0);
  if (!r.chemin) { toast(r.erreur, "ko"); $("dic-etat").textContent = r.erreur; return; }
  if (r.avertissement) toast(r.avertissement, "ko");
  DIC.dernier = r;
  $("dic-dernier").hidden = false;
  $("dic-dernier-nom").innerHTML = `<b></b> · ${court(r.duree)}`;
  $("dic-dernier-nom").firstChild.textContent = r.nom;
  $("dic-dernier-nom").title = r.chemin;
  $("dic-etat").textContent = "✓ Enregistré";
  if ($("dic-auto").checked) {
    window.transcrireFichier(r.chemin, r.nom);
  } else {
    trAjouter({ fichiers: [{ chemin: r.chemin, nom: r.nom }], ignores: 0 });
    toast(`✓ ${r.nom} enregistré et ajouté à la liste des fichiers.`, "ok");
  }
}
$("dic-pause").addEventListener("click", () => {
  if (DIC.etat === "enregistrement") { api.dictaphone.pause(true); dicEtat("pause"); }
  else if (DIC.etat === "pause") { api.dictaphone.pause(false); dicEtat("enregistrement"); }
});
$("dic-dernier-transcrire").addEventListener("click", () => DIC.dernier && window.transcrireFichier(DIC.dernier.chemin, DIC.dernier.nom));
$("dic-dernier-dossier").addEventListener("click", () => DIC.dernier && api.ouvrir_dossier(DIC.dernier.chemin));
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "r" && outilActif === "transcription") { e.preventDefault(); trOnglet("dictaphone"); dicBasculer(); }
});

function dicEtat(etat, duree) {
  const change = etat !== DIC.etat;
  DIC.etat = etat;
  if (duree !== undefined) $("dic-chrono").textContent = hms(duree);
  const rec = etat === "enregistrement" || etat === "pause";
  $("dic-rec").classList.toggle("actif", rec);
  $("dic-pause").disabled = !rec;
  $("dic-pause").innerHTML = etat === "pause"
    ? '<svg viewBox="0 0 24 24" fill="currentColor"><circle cx="12" cy="12" r="6" fill="#ff4d5e"/></svg>'
    : '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="5" width="4" height="14" rx="1"/><rect x="14" y="5" width="4" height="14" rx="1"/></svg>';
  $("dic-pause").title = etat === "pause" ? "Reprendre" : "Pause";
  $("dic-micro").disabled = $("dic-actualiser").disabled = $("dic-changer").disabled = $("dic-format").disabled = rec;
  $("dic-tester").style.color = etat === "ecoute" ? "var(--succes)" : "";
  $("point-rec").hidden = etat !== "enregistrement";
  if (!change) return;
  const e = $("dic-etat");
  e.className = "chrono-etat" + (etat === "enregistrement" ? " rec" : "");
  e.textContent = { enregistrement: "Enregistrement", pause: "En pause", ecoute: "Micro prêt · parle pour tester le niveau", arret: "Prêt à enregistrer" }[etat];
  $("dic-etat-micro").textContent = etat === "arret" ? "Micro coupé" : "Micro ouvert";
  if (etat === "arret") { DIC.onde = []; dicVumetre(-90, -90); dicDessiner(); $("dic-info").textContent = ""; }
}

// ---------- Vumètre et forme d'onde ----------
const pct = (dbv) => Math.max(0, Math.min(100, (dbv + 60) / 60 * 100));
function dicVumetre(rms, crete) {
  $("dic-masque").style.width = (100 - pct(rms)) + "%";
  const maintenant = performance.now();
  if (crete >= DIC.crete || maintenant - DIC.creteT > 1500) { DIC.crete = crete; DIC.creteT = maintenant; }
  $("dic-crete").style.left = `calc(${pct(DIC.crete)}% - 1px)`;
}
function dicDessiner() {
  const c = $("dic-onde");
  const l = c.clientWidth, h = c.clientHeight;
  if (!l) return;
  const ratio = window.devicePixelRatio || 1;
  if (c.width !== l * ratio) { c.width = l * ratio; c.height = h * ratio; }
  const g = c.getContext("2d");
  g.setTransform(ratio, 0, 0, ratio, 0, 0);
  g.clearRect(0, 0, l, h);
  g.fillStyle = "#2a2f40";
  g.fillRect(0, h / 2 - 0.5, l, 1);
  const pas = 3, n = Math.floor(l / pas);
  const points = DIC.onde.slice(-n);
  const couleurBase = DIC.etat === "enregistrement" ? "#a594ff" : DIC.etat === "pause" ? "#5d6378" : "#6f7690";
  points.forEach((v, i) => {
    // Échelle racine : les voix calmes restent visibles.
    const a = Math.max(1, Math.sqrt(v) * (h / 2 - 2));
    g.fillStyle = v > 0.95 ? "#ff6b6b" : couleurBase;
    g.fillRect(l - (points.length - i) * pas, h / 2 - a, pas - 1, a * 2);
  });
}
sur("dic_niveau", (e) => {
  if (e.etat !== DIC.etat) dicEtat(e.etat, e.duree);
  else if (e.etat === "enregistrement" || e.etat === "pause") $("dic-chrono").textContent = hms(e.duree);
  DIC.onde.push(...e.onde);
  if (DIC.onde.length > 2000) DIC.onde = DIC.onde.slice(-1000);
  dicVumetre(e.rms, e.crete);
  dicDessiner();
  const info = $("dic-info");
  if (e.sature) { info.innerHTML = '<span class="sature">⚠ Micro saturé : baisse le volume du micro dans Windows</span>'; DIC.satureT = performance.now(); }
  else if (!DIC.satureT || performance.now() - DIC.satureT > 2500) {
    info.textContent = $("dic-agc").checked && e.gain_auto > 0.5 ? `Gain auto +${e.gain_auto.toFixed(0)} dB` : "";
  }
});
window.addEventListener("resize", dicDessiner);

// ---------- Dossier et format ----------
$("dic-changer").addEventListener("click", async () => {
  const d = await api.choisir_dossier();
  if (d) { R.dic_dossier = d; $("dic-dossier").value = d; sauverReglages(); }
});
$("dic-ouvrir").addEventListener("click", () => api.ouvrir_dossier(R.dic_dossier));
$("dic-format").addEventListener("change", () => { R.dic_format = $("dic-format").value; sauverReglages(); });
$("dic-auto").addEventListener("change", () => { R.dic_auto = $("dic-auto").checked; sauverReglages(); });

demarrage(async () => {
  const son = R.dic_son || { ...PROFILS.entretien, profil: "entretien" };
  dicAfficher(son);
  dicProfil(son.profil);
  $("dic-dossier").value = R.dic_dossier;
  $("dic-dossier").title = R.dic_dossier;
  $("dic-format").value = R.dic_format || "wav";
  $("dic-auto").checked = R.dic_auto ?? true;
  await dicMicros();
  dicDessiner();
});
// Le micro ne s'ouvre qu'une fois tout le reste prêt (onglet mémorisé inclus).
window.addEventListener("pywebviewready", () => setTimeout(dicSuivreVisibilite, 800));
