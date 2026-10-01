// Détourage : liste d'images / pages de PDF, aperçus sur damier, détourage IA ou séparation d'éléments.
const DET = { sources: [], etats: [], selection: new Set(), courant: -1, ancre: -1, mode: "ia", enCours: false };

function detListe(r) {
  if (r.erreur) toast(r.erreur, "ko");
  if (r.sources.length !== DET.sources.length || r.sources.some((s, i) => s !== DET.sources[i])) {
    DET.etats = r.sources.map((s) => { const i = DET.sources.indexOf(s); return i >= 0 ? DET.etats[i] : null; });
  }
  DET.sources = r.sources;
  DET.selection = new Set([...DET.selection].filter((i) => i < DET.sources.length));
  detRendu();
  const n = DET.sources.length;
  $("det-compte").textContent = `${n} image${n > 1 ? "s" : ""}`;
  if (DET.courant >= n) DET.courant = -1;
  if (DET.courant < 0 && n) detChoisir(0);
  if (!n) detApercus(null, null);
}
function detRendu() {
  const liste = $("det-liste");
  liste.innerHTML = "";
  if (!DET.sources.length) { liste.innerHTML = '<div class="vide">Aucune image pour l\'instant</div>'; return; }
  DET.sources.forEach((s, i) => {
    const etat = DET.etats[i];
    const l = document.createElement("div");
    l.className = "ligne-fichier" + (etat ? " " + etat.classe : "") + (DET.selection.has(i) ? " selection" : "");
    l.innerHTML = `<span class="pastille">●</span><span class="nom"></span><span></span><span class="statut"></span>`;
    l.querySelector(".nom").textContent = s;
    l.querySelector(".nom").title = s;
    l.querySelector(".statut").textContent = etat ? etat.texte : "Prête";
    l.addEventListener("click", (e) => detClic(i, e));
    liste.appendChild(l);
  });
}
function detClic(i, e) {
  if (e.shiftKey && DET.ancre >= 0) {
    DET.selection = new Set();
    for (let k = Math.min(DET.ancre, i); k <= Math.max(DET.ancre, i); k++) DET.selection.add(k);
  } else if (e.ctrlKey) {
    DET.selection.has(i) ? DET.selection.delete(i) : DET.selection.add(i);
    DET.ancre = i;
  } else {
    DET.selection = new Set([i]);
    DET.ancre = i;
  }
  detRendu();
  if (!DET.enCours) detChoisir(i, false);
}
async function detChoisir(i, selectionner = true) {
  DET.courant = i;
  if (selectionner) { DET.selection = new Set([i]); DET.ancre = i; detRendu(); }
  const r = await api.detourage.apercu(i);
  if (DET.courant !== i) return;
  if (r.erreur) { statut("det", r.erreur, "ko"); detApercus(null, null); return; }
  detApercus(r.original, null);
}
function detApercus(original, resultat) {
  for (const [id, src, vide] of [["original", original, "Sélectionne une image"], ["resultat", resultat, "—"]]) {
    const img = $("det-img-" + id);
    img.hidden = !src;
    if (src) img.src = src;
    $("det-rien-" + id).hidden = !!src;
    $("det-rien-" + id).textContent = vide;
  }
}

receveurs.detourage = async (chemins) => {
  statut("det", "Lecture des fichiers…");
  detListe(await api.detourage.ajouter(chemins));
  statut("det", "Prêt.");
};
$("det-depot").addEventListener("click", async () => detListe(await api.detourage.choisir_fichiers()));
$("det-ajout-dossier").addEventListener("click", async () => detListe(await api.detourage.choisir_dossier()));
$("det-retirer").addEventListener("click", async () => {
  if (DET.enCours || !DET.selection.size) return;
  const r = await api.detourage.retirer([...DET.selection]);
  DET.selection.clear(); DET.courant = -1;
  detListe(r);
});
$("det-vider").addEventListener("click", async () => {
  if (DET.enCours) return;
  DET.selection.clear(); DET.courant = -1;
  detListe(await api.detourage.vider());
});

// ---------- Options ----------
segmente("det-modes", null, (v) => detMode(v));
function detMode(v) {
  DET.mode = v;
  document.querySelectorAll("#det-modes button").forEach((b) => b.classList.toggle("actif", b.dataset.mode === v));
  $("det-groupe-ia").hidden = v !== "ia";
  $("det-groupe-sep").hidden = v !== "separer";
  $("det-etiquette-resultat").textContent = v === "separer" ? "ÉLÉMENTS DÉTECTÉS" : "RÉSULTAT";
  if (!DET.enCours) $("det-lancer").textContent = v === "separer" ? "Séparer les éléments" : "Détourer";
  $("det-img-resultat").hidden = true; $("det-rien-resultat").hidden = false;
  detSauver();
}
const CHAMPS_DET = ["det-modele", "det-suffixe", "det-affiner", "det-nettoyer", "det-recadrer", "det-marge", "det-sp-distance",
  "det-sp-min", "det-sp-seuil", "det-sp-bord", "det-sp-remplir", "det-sp-trou", "det-format", "det-dpi", "det-ecraser"];
function detSauver() {
  R.det = { mode: DET.mode, sortie: $("det-sortie").value };
  for (const id of CHAMPS_DET) { const c = $(id); R.det[id] = c.type === "checkbox" ? c.checked : c.value; }
  sauverReglages();
}
CHAMPS_DET.forEach((id) => $(id).addEventListener("change", detSauver));
$("det-sp-remplir").addEventListener("change", () => ($("det-sp-trou").disabled = !$("det-sp-remplir").checked));
$("det-choisir-sortie").addEventListener("click", async () => {
  const d = await api.detourage.choisir_sortie();
  if (d) { $("det-sortie").value = d; $("det-sortie").title = d; detSauver(); }
});
$("det-sortie").addEventListener("dblclick", () => { $("det-sortie").value = ""; detSauver(); });
function detSeparation() {
  return { group_distance: +$("det-sp-distance").value, min_area: +$("det-sp-min").value, ink_threshold: +$("det-sp-seuil").value,
           border: +$("det-sp-bord").value, fill_shapes: $("det-sp-remplir").checked, min_hole: +$("det-sp-trou").value };
}
$("det-apercu-sep").addEventListener("click", async () => {
  if (DET.enCours || DET.courant < 0) { statut("det", "Sélectionne une image dans la liste pour voir le découpage.", "attente"); return; }
  statut("det", "Calcul du découpage…");
  progression("det", null);
  const r = await api.detourage.apercu_decoupage(DET.courant, +$("det-dpi").value, detSeparation());
  progression("det", 0);
  if (r.erreur) { statut("det", r.erreur, "ko"); return; }
  detApercus(r.original, r.resultat);
  statut("det", `Aperçu : ${r.nb} éléments détectés (rien n'est enregistré).`);
});

// ---------- Traitement ----------
$("det-lancer").addEventListener("click", async () => {
  if (DET.enCours) { api.detourage.arreter(); $("det-lancer").textContent = "Arrêt en cours…"; $("det-lancer").disabled = true; return; }
  if (!DET.sources.length) { statut("det", "Ajoute d'abord des images ou des PDF.", "attente"); return; }
  detSauver();
  const r = await api.detourage.lancer({
    mode: DET.mode, separation: detSeparation(), modele: $("det-modele").value, format: $("det-format").value,
    dpi: +$("det-dpi").value, affiner: $("det-affiner").checked, nettoyer: $("det-nettoyer").checked,
    recadrer: $("det-recadrer").checked, marge: +$("det-marge").value, suffixe: $("det-suffixe").value,
    sortie: $("det-sortie").value.trim(), ecraser: $("det-ecraser").checked,
  });
  if (r.erreur) { statut("det", r.erreur, "ko"); return; }
  DET.enCours = true;
  DET.etats = DET.sources.map(() => null);
  detRendu();
  progression("det", 0);
  $("det-lancer").textContent = "Arrêter";
  $("det-lancer").classList.add("arret");
  $("det-ouvrir").disabled = true;
});
sur("det_progression", (e) => {
  statut("det", `[${e.i + 1}/${e.total}] ${e.message}`);
  DET.etats[e.i] = { classe: "encours", texte: "En cours…" };
  DET.courant = e.i;
  DET.selection = new Set([e.i]);
  detRendu();
  progression("det", e.i / e.total);
});
sur("det_element", (e) => {
  DET.etats[e.i] = { classe: e.ok ? "ok" : "ko", texte: (e.ok ? "✓ " : "✕ ") + e.message };
  detRendu();
  if (e.original) detApercus(e.original, e.resultat);
  progression("det", (e.i + 1) / DET.sources.length);
});
sur("det_termine", (e) => {
  DET.enCours = false;
  $("det-lancer").disabled = false;
  $("det-lancer").classList.remove("arret");
  $("det-lancer").textContent = DET.mode === "separer" ? "Séparer les éléments" : "Détourer";
  $("det-ouvrir").disabled = e.ok === 0;
  const txt = `${e.arrete ? "Arrêté" : "Terminé"} : ${e.ok} réussie(s), ${e.erreurs} erreur(s).`;
  statut("det", txt, e.erreurs ? "attente" : e.arrete ? "attente" : "ok");
  if (!e.arrete && !e.erreurs) progression("det", 1);
});
$("det-ouvrir").addEventListener("click", () => api.detourage.ouvrir_sortie());

demarrage(async () => {
  const o = await api.detourage.options();
  $("det-modele").innerHTML = o.modeles.map((m) => `<option>${echapper(m)}</option>`).join("");
  $("det-format").innerHTML = o.formats.map((m) => `<option>${echapper(m)}</option>`).join("");
  const d = R.det || {};
  for (const id of CHAMPS_DET) {
    if (d[id] === undefined) continue;
    const c = $(id);
    if (c.type === "checkbox") c.checked = d[id]; else if (c.tagName !== "SELECT" || [...c.options].some((x) => x.value === d[id])) c.value = d[id];
  }
  $("det-sortie").value = d.sortie || "";
  $("det-sortie").title = (d.sortie || "") + (d.sortie ? "\nDouble-clic pour revenir au dossier par défaut" : "");
  $("det-sp-trou").disabled = !$("det-sp-remplir").checked;
  detMode(d.mode || "ia");
});
