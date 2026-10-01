// Socle commun : navigation, réglages, événements venus de Python, dépôt de fichiers, petits utilitaires.
const $ = (id) => document.getElementById(id);
let api = null;
const R = {};                       // réglages persistants (%APPDATA%\Atelier\reglages.json)
const gestionnaires = {};
const auDemarrage = [];

const EXT_AUDIO = [".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm", ".mp4"];
const EXT_IMAGES = [".png", ".jpg", ".jpeg", ".jfif", ".webp", ".bmp", ".tif", ".tiff", ".gif", ".heic", ".heif", ".avif", ".pdf"];
const EXT_TEXTE = [".txt", ".md"];

function sur(type, fn) { gestionnaires[type] = fn; }
function demarrage(fn) { auDemarrage.push(fn); }

// Appelé par Python avec un paquet d'événements.
window.recevoir = (paquet) => {
  for (const ev of paquet) {
    const fn = gestionnaires[ev.type];
    if (fn) { try { fn(ev); } catch (e) { console.error(ev.type, e); } }
  }
};

let minuterieReglages = null;
function sauverReglages() {
  clearTimeout(minuterieReglages);
  minuterieReglages = setTimeout(() => api && api.enregistrer_reglages(R), 400);
}

// ---------- Navigation ----------
let outilActif = "transcription";
function outil(nom) {
  outilActif = nom;
  document.querySelectorAll(".rail .onglet").forEach((b) => b.classList.toggle("actif", b.dataset.outil === nom));
  document.querySelectorAll(".vue").forEach((v) => { v.hidden = v.id !== "vue-" + nom; });
  R.outil = nom;
  sauverReglages();
  document.dispatchEvent(new CustomEvent("outil", { detail: nom }));
}
document.querySelectorAll(".rail .onglet").forEach((b) => b.addEventListener("click", () => outil(b.dataset.outil)));
document.addEventListener("keydown", (e) => {
  if ((e.ctrlKey || e.metaKey) && ["1", "2", "3"].includes(e.key)) {
    outil(["transcription", "mise-en-page", "detourage"][+e.key - 1]);
    e.preventDefault();
  }
});

// ---------- Glisser-déposer (les chemins complets arrivent par Python) ----------
let compteurGlisse = 0;
document.addEventListener("dragenter", (e) => { e.preventDefault(); compteurGlisse++; document.body.classList.add("glisse"); });
document.addEventListener("dragleave", () => { if (--compteurGlisse <= 0) { compteurGlisse = 0; document.body.classList.remove("glisse"); } });
document.addEventListener("dragover", (e) => e.preventDefault());
document.addEventListener("drop", (e) => { e.preventDefault(); compteurGlisse = 0; document.body.classList.remove("glisse"); });

const extension = (c) => { const m = /\.[^.\\/]+$/.exec(c); return m ? m[0].toLowerCase() : ""; };
const receveurs = {};  // outil → fonction(chemins)

// Chaque fichier va à l'outil qui sait le traiter ; les dossiers vont à l'outil affiché.
function router(chemins) {
  const groupes = { transcription: [], detourage: [], "mise-en-page": [] };
  for (const c of chemins) {
    const ext = extension(c);
    if (EXT_AUDIO.includes(ext)) groupes.transcription.push(c);
    else if (EXT_IMAGES.includes(ext)) groupes.detourage.push(c);
    else if (EXT_TEXTE.includes(ext)) groupes["mise-en-page"].push(c);
    else groupes[outilActif].push(c);
  }
  const cibles = Object.keys(groupes).filter((k) => groupes[k].length);
  if (cibles.length && !cibles.includes(outilActif)) outil(cibles[0]);
  for (const k of cibles) receveurs[k](groupes[k]);
}
sur("depot", (ev) => router(ev.chemins));

// ---------- Utilitaires ----------
function statut(prefixe, texte, classe = "") {
  const s = $(prefixe + "-statut");
  s.className = "statut " + classe;
  s.firstElementChild.textContent = texte;
  s.firstElementChild.title = texte;
}
function progression(prefixe, valeur) {
  const barre = $(prefixe + "-progression");
  const pct = $(prefixe + "-statut").lastElementChild;
  if (valeur === null) { barre.classList.add("indeterminee"); pct.textContent = ""; return; }
  barre.classList.remove("indeterminee");
  barre.firstElementChild.style.width = (valeur * 100) + "%";
  pct.textContent = valeur > 0 ? Math.round(valeur * 100) + " %" : "";
}
function badge(id, texte, classe) {
  $(id).className = "badge " + classe;
  $(id).querySelector("span").textContent = texte;
}
function toast(texte, classe = "") {
  const t = document.createElement("div");
  t.className = "toast " + classe;
  t.textContent = texte;
  $("toasts").appendChild(t);
  setTimeout(() => t.remove(), 4500);
}
function hms(s) {
  s = Math.max(0, Math.floor(s));
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`;
}
function court(s) {
  s = Math.floor(s);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = s % 60;
  return h ? `${h}:${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}` : `${String(m).padStart(2, "0")}:${String(x).padStart(2, "0")}`;
}
function raccourcir(t, n = 34) { return t.length <= n ? t : t.slice(0, n - 1) + "…"; }
function echapper(t) { return t.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
function segmente(id, valeur, surChoix) {
  const conteneur = $(id);
  const choisir = (v) => conteneur.querySelectorAll("button").forEach((b) => b.classList.toggle("actif", (b.dataset.v ?? b.dataset.mode ?? b.dataset.onglet) === v));
  if (valeur != null) choisir(valeur);
  conteneur.addEventListener("click", (e) => {
    const b = e.target.closest("button");
    if (!b || b.disabled || conteneur.classList.contains("verrou")) return;
    const v = b.dataset.v ?? b.dataset.mode ?? b.dataset.onglet;
    choisir(v);
    surChoix(v);
  });
  return choisir;
}
function remplirCurseur(input) {
  const p = (input.value - input.min) / (input.max - input.min) * 100;
  input.style.setProperty("--p", p + "%");
}
async function copier(texte) {
  try { await navigator.clipboard.writeText(texte); return true; } catch (e) { /* repli ci-dessous */ }
  const zone = document.createElement("textarea");
  zone.value = texte; document.body.appendChild(zone); zone.select();
  const ok = document.execCommand("copy"); zone.remove();
  return ok;
}

// Copie du HTML mis en forme (gras, couleurs, titres conservés au collage dans Google Docs ou Word).
function copierMisEnForme(html) {
  const zone = document.createElement("div");
  zone.contentEditable = "true";
  zone.style.cssText = "position:fixed;left:-99999px;top:0;background:#fff;color:#000;font:11pt Arial,sans-serif";
  zone.innerHTML = html;
  document.body.appendChild(zone);
  const sel = window.getSelection();
  const plage = document.createRange();
  plage.selectNodeContents(zone);
  sel.removeAllRanges(); sel.addRange(plage);
  const ok = document.execCommand("copy");
  sel.removeAllRanges();
  zone.remove();
  return ok;
}
// Copie puis ouvre un nouveau document Google Docs : il ne reste qu'à faire Ctrl+V.
function versGoogleDocs(html) {
  if (!copierMisEnForme(html)) { toast("Impossible de copier le texte.", "ko"); return false; }
  api.ouvrir_google_docs();
  toast(`✓ Texte copié. Dans le nouveau Google Docs qui s'ouvre, fais ${coller()}.`, "ok");
  return true;
}

// Sur Mac, la touche Commande (⌘) remplace Ctrl dans les raccourcis affichés (sauf Ctrl+Espace).
let surMac = false;
const coller = () => (surMac ? "⌘V" : "Ctrl+V");
function adapterMac() {
  surMac = true;
  document.body.classList.add("mac");
  const remplacer = (t) => t.replace(/Ctrl\+(?!Espace)/g, "⌘");
  document.querySelectorAll("[title]").forEach((el) => { el.title = remplacer(el.title); });
  document.querySelectorAll(".modale small").forEach((el) => { el.textContent = remplacer(el.textContent); });
}

window.addEventListener("pywebviewready", async () => {
  api = window.pywebview.api;
  Object.assign(R, await api.reglages());
  if ((await api.systeme()).mac) adapterMac();
  for (const fn of auDemarrage) { try { await fn(); } catch (e) { console.error(e); } }
  outil(R.outil || "transcription");
  const depart = await api.fichiers_de_depart();
  if (depart.length) router(depart);
});
