const boot = JSON.parse(document.getElementById("boot").textContent || "{}");
const hints = boot.hints || {};
const rowsEl = document.getElementById("rows");
const drawer = document.getElementById("drawer");
const drawerBg = document.getElementById("drawer-bg");
const drawerBody = document.getElementById("drawer-body");
const subjectEl = document.getElementById("d-subject");

/** @type {number | null} */
let openId = null;
/** @type {object[]} */
let lotsCache = [];
let sortKey = "date";
let sortDir = "desc";

function val(id) {
  const el = document.getElementById(id);
  return el ? String(el.value || "").trim() : "";
}

function params() {
  const data = new URLSearchParams();
  const map = {
    q: "f-q",
    amount_min: "f-min",
    amount_max: "f-max",
    keyword: "f-keyword",
    profile: "f-profile",
    status: "f-status",
    has_phone: "f-phone",
    source: "f-source",
    fz: "f-fz",
    date_from: "f-from",
    date_to: "f-to",
    region: "f-region",
  };
  for (const [key, id] of Object.entries(map)) {
    const value = val(id);
    if (value) data.set(key, value);
  }
  return data;
}

function esc(text) {
  return String(text ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function regionLabel(lot) {
  const rows = boot.regions || [];
  const found = rows.find((item) => item.code === lot.region_code);
  return found?.name || lot.region || "не указан";
}

function sortLots(lots) {
  const dir = sortDir === "asc" ? 1 : -1;
  return [...lots].sort((left, right) => {
    if (sortKey === "date") {
      const a = String(left.signed_at || left.published_at || "");
      const b = String(right.signed_at || right.published_at || "");
      if (a === b) return 0;
      return a < b ? -dir : dir;
    }
    if (sortKey === "profile") {
      const a = (left.profile_labels || []).join(", ");
      const b = (right.profile_labels || []).join(", ");
      return a.localeCompare(b, "ru") * dir;
    }
    return (Number(left.amount || 0) - Number(right.amount || 0)) * dir;
  });
}

function markSortHeaders() {
  document.querySelectorAll("th[data-sort]").forEach((th) => {
    const key = th.getAttribute("data-sort");
    th.setAttribute(
      "aria-sort",
      key === sortKey ? (sortDir === "asc" ? "ascending" : "descending") : "none",
    );
  });
}

function showLots(lots) {
  lotsCache = lots;
  markSortHeaders();
  renderRows(sortLots(lotsCache));
}

function renderRows(lots) {
  if (!lots.length) {
    rowsEl.innerHTML = `<tr><td colspan="9" class="empty">По этому отбору заказов нет. Снимите фильтры или подождите новый сбор.</td></tr>`;
    return;
  }
  rowsEl.innerHTML = lots
    .map((lot) => {
      const hot = lot.hot ? ' class="hot"' : "";
      return `<tr data-id="${lot.id}"${hot} title="${esc(hints.buttons?.open_card || "")}">
        <td>${esc(lot.date)}</td>
        <td class="region-cell">${esc(regionLabel(lot))}</td>
        <td class="num">${esc(lot.amount_text)}</td>
        <td class="subj">${esc(lot.subject)}</td>
        <td>${esc(lot.customer_name)}</td>
        <td>${esc(lot.winner_name)}</td>
        <td>${esc(lot.phone)}</td>
        <td>${esc((lot.profile_labels || []).join(", "))}</td>
        <td><span class="pill pill-${esc(lot.status)}" title="${esc(hints.statuses?.[lot.status] || "")}">${esc(lot.status_label)}</span></td>
      </tr>`;
    })
    .join("");
}

function renderTiles(tiles) {
  const map = {
    found: tiles.found,
    profile: tiles.profile,
    winner: tiles.winner,
    no_contact: tiles.no_contact,
  };
  for (const [key, value] of Object.entries(map)) {
    const el = document.getElementById(`tile-${key}`);
    if (el) el.textContent = String(value ?? 0);
  }
}

async function loadLots() {
  rowsEl.innerHTML = `<tr><td colspan="9" class="empty">Обновляем список…</td></tr>`;
  const response = await fetch(`/api/lots?${params().toString()}`, { credentials: "same-origin" });
  if (response.status === 401) {
    window.location.href = "/login";
    return;
  }
  const data = await response.json();
  renderTiles(data.tiles || {});
  showLots(data.lots || []);
}

function orgBlock(title, hint, org, fallbackName, fallbackInn) {
  if (!org) {
    return `<section class="block" title="${esc(hint)}">
      <h3>${esc(title)}</h3>
      <p><strong>${esc(fallbackName)}</strong></p>
      <p>ИНН: ${esc(fallbackInn)}</p>
      <p class="muted">Реквизиты налоговой ещё не подтянулись.</p>
    </section>`;
  }
  return `<section class="block" title="${esc(hint)}">
    <h3>${esc(title)}</h3>
    <p><strong>${esc(org.name || fallbackName)}</strong></p>
    <p>ИНН: ${esc(fallbackInn)}</p>
    <p>Статус: ${esc(org.status)}</p>
    <p>Директор: ${esc(org.director)}</p>
    <p>Адрес: ${esc(org.address)}</p>
    <p>ОГРН: ${esc(org.ogrn)}</p>
  </section>`;
}

function contactKind(item) {
  if (item.type === "phone") return "телефон";
  if (item.type === "email") return "почта";
  if (item.type === "website") return "сайт";
  return item.type;
}

function contactValue(item) {
  const value = esc(item.value);
  if (item.type === "phone") {
    return `<a href="tel:${value}">${value}</a>`;
  }
  if (item.type === "email") {
    return `<a href="mailto:${value}">${value}</a>`;
  }
  if (item.type === "website") {
    return `<a href="${value}" target="_blank" rel="noopener">${value.replace(/^https?:\/\//, "")}</a>`;
  }
  return value;
}

function contactBucket(item) {
  const src = item.source || "";
  if (src === "winner_site") return "site";
  if (src === "checko") return "checko";
  if (src === "dadata") return "dadata";
  if (src === "document_ocr") return "file";
  if (src.startsWith("eis") || src === "email_domain") return "eis";
  return "other";
}

function contactLine(item) {
  return `<li><span class="ck">${esc(contactKind(item))}</span>${contactValue(item)}</li>`;
}

function sortContacts(rows) {
  const rank = { website: 0, phone: 1, email: 2 };
  return [...rows].sort((a, b) => (rank[a.type] ?? 9) - (rank[b.type] ?? 9));
}

function contactKey(item) {
  return `${item.type}:${item.value}`;
}

function takeUnseen(rows, seen) {
  const out = [];
  for (const item of rows) {
    const key = contactKey(item);
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

function splitCompact(rows) {
  const phones = [...rows.filter((item) => item.type === "phone")].sort((a, b) => {
    const am = String(a.value || "").startsWith("+79") ? 0 : 1;
    const bm = String(b.value || "").startsWith("+79") ? 0 : 1;
    return am - bm;
  });
  const emails = rows.filter((item) => item.type === "email");
  const sites = rows.filter((item) => item.type === "website");
  const other = rows.filter((item) => !["phone", "email", "website"].includes(item.type));
  const main = [...sites.slice(0, 1), ...phones.slice(0, 2), ...emails.slice(0, 1), ...other];
  const extra = [...sites.slice(1), ...phones.slice(2), ...emails.slice(1)];
  return { main, extra };
}

function contactGroup(title, tip, rows, compact = false) {
  const seen = new Set();
  const unique = sortContacts(
    rows.filter((item) => {
      const key = contactKey(item);
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    }),
  );
  if (!unique.length) return "";
  const packed = compact ? splitCompact(unique) : { main: unique, extra: [] };
  const extra = packed.extra.length
    ? `<details class="contact-more"><summary>ещё ${packed.extra.length}</summary><ul class="contacts">${packed.extra.map(contactLine).join("")}</ul></details>`
    : "";
  return `<div class="contact-group">
    <h4 title="${esc(tip)}">${esc(title)}</h4>
    <ul class="contacts">${packed.main.map(contactLine).join("")}</ul>
    ${extra}
  </div>`;
}

function dadataContactRows(org) {
  if (!org) return [];
  const rows = [];
  for (const value of org.phones || []) {
    rows.push({ type: "phone", value });
  }
  for (const value of org.emails || []) {
    rows.push({ type: "email", value });
  }
  if (org.website) rows.push({ type: "website", value: org.website });
  return rows;
}

function contactsHtml(lot) {
  const rows = lot.contacts || [];
  const buckets = { site: [], eis: [], checko: [], dadata: [], file: [], other: [] };
  for (const item of rows) {
    buckets[contactBucket(item)].push(item);
  }
  const dadataRows = buckets.dadata.length ? buckets.dadata : dadataContactRows(lot.winner_org);
  const seen = new Set();
  const parts = [
    contactGroup(
      "Сайт фирмы",
      "Зашли на сайт победителя, убедились что это их фирма, сняли контакты со страниц.",
      takeUnseen(buckets.site, seen),
    ),
    contactGroup(
      "Файлы закупки",
      "Контакты из договора и сканов.",
      takeUnseen(buckets.file, seen),
    ),
    contactGroup(
      "Checko",
      "Контакты из карточки Checko по ИНН победителя. Лишние номера свёрнуты.",
      takeUnseen(buckets.checko, seen),
      true,
    ),
    contactGroup(
      "ЕИС",
      "Контакты с карточки госзакупок.",
      takeUnseen(buckets.eis, seen),
    ),
    contactGroup(
      "DaData",
      "Контакты из DaData по ИНН. Если справочник телефон не отдаёт — раздела нет.",
      takeUnseen(dadataRows, seen),
    ),
  ];
  if (buckets.other.length) {
    parts.push(contactGroup("Ещё", "Другие источники.", takeUnseen(buckets.other, seen)));
  }
  const html = parts.filter(Boolean).join("");
  return html ? `<div class="contact-groups">${html}</div>` : "";
}

function statusButtons(lot) {
  const labels = boot.statuses || {};
  return Object.entries(labels)
    .map(([key, label]) => {
      const on = lot.status === key ? " go compact" : " ghost";
      const tip = hints.buttons?.[key] || hints.statuses?.[key] || "";
      return `<button type="button" class="${on.trim()}" data-status="${esc(key)}" title="${esc(tip)}">${esc(label)}</button>`;
    })
    .join("");
}

function docsFoldMeta(rows) {
  const look = rows.filter((item) => item.value === "read").length;
  const skip = rows.length - look;
  if (!rows.length) return "";
  if (look && skip) return `${look} смотреть · ${skip} не надо`;
  if (look) return `${look} смотреть`;
  return `${skip} не надо`;
}

function documentsHtml(lot) {
  const rows = [...(lot.documents || [])].sort((a, b) => (a.rank ?? 50) - (b.rank ?? 50));
  if (!rows.length) return "";
  return `<ul class="docs">${rows
    .map((item) => {
      const name = esc(item.filename || "файл");
      const look = item.value === "read";
      const mark = look ? "смотреть" : "не надо";
      const link = item.url
        ? `<a href="${esc(item.url)}" target="_blank" rel="noopener">${name}</a>`
        : name;
      const sum = item.summary
        ? `<p class="doc-sum">${esc(item.summary)}</p>`
        : item.ocr_status === "pending"
          ? `<p class="doc-sum muted">Ещё не читали.</p>`
          : "";
      return `<li class="${look ? "doc-read" : "doc-skip"}">
        <div class="doc-head"><strong>${link}</strong><span class="doc-mark">${esc(mark)}</span></div>
        ${sum}
      </li>`;
    })
    .join("")}</ul>`;
}

function analysisHtml(lot) {
  const a = lot.analysis;
  if (!a) return "";
  const rows = (a.works || []).filter((item) => item.profile && item.profile !== "object");
  const works = rows
    .map((item) => {
      const pay = item.amount_text ? ` · <span class="work-sum">${esc(item.amount_text)}</span>` : "";
      const ev = item.evidence ? ` — ${esc(item.evidence)}` : "";
      return `<li><strong>${esc(item.label)}</strong>${ev}${pay}</li>`;
    })
    .join("");
  const smeta = (a.smeta || [])
    .map((item) => {
      const pay = item.amount_text ? ` · <span class="work-sum">${esc(item.amount_text)}</span>` : "";
      return `<li><strong>${esc(item.title || "смета")}</strong>${pay}</li>`;
    })
    .join("");
  const minus = (a.minus || []).length
    ? `<p class="muted">Рядом не наши темы: ${esc(a.minus.join(", "))}.</p>`
    : "";
  const via = a.source || "";
  return `<section class="block analysis analysis-${esc(a.verdict || "maybe")}">
    <h3>Насколько это наш заказ</h3>
    <p class="verdict">${esc(a.label || "")}</p>
    <p>${esc(a.why || "")}</p>
    ${smeta ? `<p class="offer-label">В смете</p><ul class="work-hits">${smeta}</ul>` : ""}
    ${works ? `<p class="offer-label">Можем предложить</p><ul class="work-hits">${works}</ul>` : ""}
    ${minus}
    ${via ? `<p class="muted">${esc(via)}</p>` : ""}
  </section>`;
}

function renderCard(lot) {
  subjectEl.textContent = lot.subject;
  const eis = lot.url
    ? `<a class="go compact" href="${esc(lot.url)}" target="_blank" rel="noopener" title="${esc(hints.buttons?.open_eis || "")}">Открыть на госзакупках</a>`
    : "";
  const contacts = contactsHtml(lot);
  const docs = documentsHtml(lot);
  drawerBody.innerHTML = `
    <p class="lead-card">${esc(lot.amount_text)} · ${esc(lot.date)} · ${esc(lot.region)}</p>
    <p class="why" title="${esc(hints.headings?.card || "")}">${esc(lot.reason)}</p>
    ${analysisHtml(lot)}
    <div class="grid-2">
      ${orgBlock("Победитель", hints.headings?.winner, lot.winner_org, lot.winner_name, lot.winner_inn)}
      ${orgBlock("Заказчик", hints.headings?.customer, lot.customer_org, lot.customer_name, lot.customer_inn)}
    </div>
    ${
      contacts
        ? `<section class="block" title="${esc(hints.columns?.contact || "")}">
      <h3>Контакты победителя</h3>
      ${contacts}
    </section>`
        : ""
    }
    ${
      docs
        ? `<section class="block fold-block" title="${esc(hints.headings?.documents || "")}">
      <button type="button" class="fold-head" aria-expanded="false">
        <h3>Файлы закупки</h3>
        <span class="fold-meta" data-base="${esc(docsFoldMeta(lot.documents || []))}">${esc(docsFoldMeta(lot.documents || []))} · развернуть</span>
      </button>
      <div class="fold-body" hidden>${docs}</div>
    </section>`
        : ""
    }
    <section class="block">
      <h3>Статус</h3>
      <div class="status-row">${statusButtons(lot)}</div>
    </section>
    <div class="drawer-actions">${eis}</div>
  `;
}

async function openCard(id) {
  openId = id;
  drawer.classList.add("open");
  drawerBg.classList.add("open");
  drawer.hidden = false;
  drawerBg.hidden = false;
  drawerBody.innerHTML = `<p class="muted">Открываем карточку…</p>`;
  const response = await fetch(`/api/lots/${id}`, { credentials: "same-origin" });
  if (!response.ok) {
    drawerBody.innerHTML = `<p class="err">Не получилось открыть заказ.</p>`;
    return;
  }
  renderCard(await response.json());
}

function closeCard() {
  openId = null;
  drawer.classList.remove("open");
  drawerBg.classList.remove("open");
  drawer.hidden = true;
  drawerBg.hidden = true;
}

async function setStatus(status) {
  if (!openId) return;
  const response = await fetch(`/api/lots/${openId}/status`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF": boot.csrf },
    body: JSON.stringify({ status, csrf: boot.csrf }),
  });
  if (!response.ok) {
    window.alert("Статус не сохранился. Обновите страницу и попробуйте ещё раз.");
    return;
  }
  await openCard(openId);
  await loadLots();
}

function clearFilters() {
  for (const id of [
    "f-q",
    "f-min",
    "f-max",
    "f-keyword",
    "f-profile",
    "f-status",
    "f-phone",
    "f-source",
    "f-fz",
    "f-from",
    "f-to",
    "f-region",
  ]) {
    const el = document.getElementById(id);
    if (el) el.value = "";
  }
}

document.querySelectorAll(".th-sort").forEach((btn) => {
  btn.addEventListener("click", (event) => {
    event.stopPropagation();
    const key = btn.getAttribute("data-sort") || "date";
    if (sortKey === key) {
      sortDir = sortDir === "desc" ? "asc" : "desc";
    } else {
      sortKey = key;
      sortDir = key === "profile" ? "asc" : "desc";
    }
    showLots(lotsCache);
  });
});
document.getElementById("apply")?.addEventListener("click", () => {
  void loadLots();
});
document.getElementById("reset")?.addEventListener("click", () => {
  clearFilters();
  void loadLots();
});
let searchTimer = 0;
function runSearchNow() {
  window.clearTimeout(searchTimer);
  void loadLots();
}
function scheduleSearch() {
  window.clearTimeout(searchTimer);
  searchTimer = window.setTimeout(() => void loadLots(), 280);
}
document.getElementById("f-q")?.addEventListener("input", scheduleSearch);
document.getElementById("f-q")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runSearchNow();
  }
});
document.getElementById("filters-body")?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    runSearchNow();
  }
});
for (const id of ["f-keyword", "f-profile", "f-status", "f-phone", "f-source", "f-fz", "f-region", "f-from", "f-to"]) {
  document.getElementById(id)?.addEventListener("change", () => {
    void loadLots();
  });
}
for (const id of ["f-min", "f-max"]) {
  document.getElementById(id)?.addEventListener("change", () => {
    void loadLots();
  });
}
document.getElementById("close-card")?.addEventListener("click", closeCard);
drawerBg?.addEventListener("click", closeCard);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeCard();
});
rowsEl?.addEventListener("click", (event) => {
  const row = event.target.closest("tr[data-id]");
  if (!row) return;
  void openCard(Number(row.dataset.id));
});
drawerBody?.addEventListener("click", (event) => {
  const fold = event.target.closest(".fold-head");
  if (fold) {
    const open = fold.getAttribute("aria-expanded") === "true";
    fold.setAttribute("aria-expanded", open ? "false" : "true");
    const body = fold.parentElement?.querySelector(".fold-body");
    const meta = fold.querySelector(".fold-meta");
    if (body) body.hidden = open;
    if (meta) {
      const base = meta.dataset.base || meta.textContent.replace(/\s·\s(?:развернуть|свернуть)$/, "");
      meta.textContent = open ? `${base} · развернуть` : `${base} · свернуть`;
    }
    return;
  }
  const btn = event.target.closest("button[data-status]");
  if (!btn) return;
  void setStatus(btn.dataset.status);
});
document.querySelectorAll(".tile").forEach((tile) => {
  tile.addEventListener("click", () => {
    const kind = tile.getAttribute("data-tile");
    const phone = document.getElementById("f-phone");
    if (kind === "no_contact" && phone) phone.value = "0";
    if (kind === "all") {
      clearFilters();
      void loadLots();
      return;
    }
    void loadLots();
  });
});

const FILTERS_KEY = "radar_filters_open";
function setFiltersOpen(open) {
  const win = document.getElementById("filter-window");
  const body = document.getElementById("filters-body");
  const btn = document.getElementById("filters-toggle");
  const meta = document.getElementById("filters-meta");
  if (!win || !body || !btn) return;
  win.classList.toggle("open", open);
  btn.setAttribute("aria-expanded", open ? "true" : "false");
  if (meta) meta.textContent = open ? "свернуть" : "развернуть";
  try {
    localStorage.setItem(FILTERS_KEY, open ? "1" : "0");
  } catch {
    /* private mode */
  }
}
document.getElementById("filters-toggle")?.addEventListener("click", () => {
  const btn = document.getElementById("filters-toggle");
  const open = btn?.getAttribute("aria-expanded") !== "true";
  setFiltersOpen(open);
});
try {
  setFiltersOpen(localStorage.getItem(FILTERS_KEY) === "1");
} catch {
  setFiltersOpen(false);
}

void loadLots();
