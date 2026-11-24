"use strict";

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
}
const INIT_DATA = tg ? tg.initData : "";

const REVEAL_KEY = "pz_revealed";

const state = {
  view: "categories", category: null, onlyUndone: false, item: null, back: null,
  revealed: JSON.parse(sessionStorage.getItem(REVEAL_KEY) || "{}"),
};

async function api(path, opts = {}) {
  const resp = await fetch(path, {
    ...opts,
    headers: { "Authorization": "tma " + INIT_DATA, ...(opts.headers || {}) },
  });
  if (!resp.ok) {
    const err = new Error("HTTP " + resp.status);
    err.status = resp.status;
    throw err;
  }
  return resp.json();
}

async function apiBlob(path) {
  const resp = await fetch(path, { headers: { "Authorization": "tma " + INIT_DATA } });
  if (!resp.ok) throw new Error("HTTP " + resp.status);
  return URL.createObjectURL(await resp.blob());
}

const viewEl = document.getElementById("view");
const backBtn = document.getElementById("back");

function showError(text) {
  const el = document.getElementById("error");
  el.textContent = text;
  el.hidden = false;
}

function setHeader(title, subtitle, showBack) {
  document.getElementById("title").textContent = title;
  document.getElementById("subtitle").textContent = subtitle || "";
  backBtn.hidden = !showBack;
}

function el(tag, className, text) {
  const e = document.createElement(tag);
  if (className) e.className = className;
  if (text !== undefined) e.textContent = text;
  return e;
}

function isHidden(item) {
  return item.spoiler && !item.done && !(item.id in state.revealed);
}

function displayTitle(item) {
  return isHidden(item) ? "🔒 Скрыто — нажми, чтобы открыть"
                      : (state.revealed[item.id] || item.title);
}

function itemRow(item, num, { onToggle, onOpen }) {
  const li = el("li", "list-item item" + (item.done ? " item-done" : ""));
  const mark = el("span", "item-mark", item.done ? "✅" : "⬜");
  mark.addEventListener("click", (e) => { e.stopPropagation(); onToggle(); });
  li.append(mark, el("span", "item-num muted", num + "."),
            el("span", "item-title", displayTitle(item)));
  li.addEventListener("click", onOpen);
  return li;
}

// --- экран категорий ---

function searchForm() {
  const form = el("form", "search-form");
  const input = el("input");
  input.type = "search";
  input.placeholder = "Поиск по пунктам";
  const btn = el("button", "btn btn-inline", "Найти");
  btn.type = "submit";
  form.append(input, btn);
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    const q = input.value.trim();
    if (q) showSearch(q).catch(showError);
  });
  return form;
}

async function showCategories() {
  state.view = "categories";
  state.category = null;
  state.back = null;
  const data = await api("/api/summary");
  setHeader("Полный зачёт", `Общий прогресс: ${data.percent}% (${data.done}/${data.total})`, false);
  viewEl.innerHTML = "";
  viewEl.appendChild(searchForm());
  const list = el("ul", "list");
  for (const cat of data.categories) {
    const li = el("li", "list-item");
    li.append(el("span", "item-title", cat.title),
              el("span", "item-count muted", `${cat.done}/${cat.total}`));
    li.addEventListener("click", () => showCategory(cat.id).catch(showError));
    list.appendChild(li);
  }
  viewEl.appendChild(list);

  const prog = el("button", "btn", "Мой прогресс");
  prog.addEventListener("click", () => showProgress().catch(showError));
  viewEl.appendChild(prog);
}

// --- экран прогресса ---

function progressBar(percent) {
  const bar = el("div", "bar");
  const fill = el("div", "bar-fill");
  fill.style.width = percent + "%";
  bar.appendChild(fill);
  return bar;
}

async function showProgress() {
  const data = await api("/api/summary");
  state.view = "progress";
  state.back = showCategories;
  setHeader("Мой прогресс", `${data.percent}% — всего ${data.done}/${data.total}`, true);
  viewEl.innerHTML = "";

  viewEl.appendChild(progressBar(data.percent));

  const list = el("ul", "list");
  for (const cat of data.categories) {
    const li = el("li", "list-item progress-item");
    const head = el("div", "progress-head");
    head.append(el("span", "item-title", cat.title),
                el("span", "item-count muted", `${cat.done}/${cat.total} (${cat.percent}%)`));
    li.append(head, progressBar(cat.percent));
    li.addEventListener("click", () => showCategory(cat.id).catch(showError));
    list.appendChild(li);
  }
  viewEl.appendChild(list);
}

// --- экран категории ---

function renderCategory() {
  const cat = state.category;
  setHeader(cat.title, `${cat.done}/${cat.total} (${cat.percent}%)`, true);
  viewEl.innerHTML = "";

  const items = state.onlyUndone ? cat.items.filter(i => !i.done) : cat.items;
  const list = el("ul", "list");
  for (const item of items) {
    const num = cat.items.indexOf(item) + 1;
    list.appendChild(itemRow(item, num, {
      onToggle: () => toggleItem(item),
      onOpen: () => {
        if (isHidden(item)) {
          revealItem(item).catch(showError);  // первое касание только раскрывает название
        } else {
          showItem(item.id, () => showCategory(cat.id)).catch(showError);
        }
      },
    }));
  }
  viewEl.appendChild(list);

  if (!items.length && state.onlyUndone) {
    viewEl.appendChild(el("p", "muted center", "Всё собрано! 🎉"));
  }

  const filter = el("button", "btn",
    state.onlyUndone ? "Показать все" : "Только неотмеченные");
  filter.addEventListener("click", () => {
    state.onlyUndone = !state.onlyUndone;
    renderCategory();
  });
  viewEl.appendChild(filter);
}

async function showCategory(catId) {
  const cat = await api(`/api/categories/${catId}`);
  state.view = "category";
  state.category = cat;
  state.back = showCategories;
  renderCategory();
}

async function revealItem(item) {
  if (!item.title) {
    item.title = (await api(`/api/items/${item.id}`)).title;
  }
  state.revealed[item.id] = item.title;
  sessionStorage.setItem(REVEAL_KEY, JSON.stringify(state.revealed));
  renderCategory();
}

async function toggleItem(item) {
  if (!item.title) {  // скрытый спойлер: название нужно сразу после отметки
    item.title = (await api(`/api/items/${item.id}`)).title;
  }
  const res = await api(`/api/items/${item.id}/toggle`, { method: "POST" });
  item.done = res.done;
  const cat = state.category;
  cat.done = cat.items.filter(i => i.done).length;
  cat.percent = cat.total ? Math.floor(cat.done * 100 / cat.total) : 0;
  renderCategory();
}

// --- экран поиска ---

function renderSearch() {
  const data = state.search;
  let subtitle = `найдено ${data.total}`;
  if (data.total > data.results.length) {
    subtitle += `, показаны первые ${data.results.length} — уточни запрос`;
  }
  setHeader(`Поиск: «${data.query}»`, subtitle, true);
  viewEl.innerHTML = "";

  if (!data.results.length) {
    viewEl.appendChild(el("p", "muted center", "Ничего не найдено. Попробуй другое слово."));
    return;
  }
  const list = el("ul", "list");
  for (const item of data.results) {
    const li = itemRow(item, "", {
      onToggle: async () => {
        await api(`/api/items/${item.id}/toggle`, { method: "POST" });
        await showSearch(data.query);  // спойлер после снятия отметки должен пропасть из выдачи
      },
      onOpen: () => showItem(item.id, () => showSearch(data.query)).catch(showError),
    });
    li.appendChild(el("span", "item-count muted", item.category));
    list.appendChild(li);
  }
  viewEl.appendChild(list);
}

async function showSearch(query) {
  const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
  state.view = "search";
  state.search = data;
  state.back = showCategories;
  renderSearch();
}

// --- экран карточки ---

async function showItem(itemId, backFn) {
  const item = await api(`/api/items/${itemId}`);
  state.view = "item";
  state.item = item;
  state.back = backFn;
  renderItem();
}

function renderItem() {
  const item = state.item;
  setHeader(item.title, `Категория: ${item.category}`, true);
  viewEl.innerHTML = "";

  viewEl.appendChild(el("p", "card-status",
    item.done ? "Статус: ✅ отмечено" : "Статус: ⬜ не отмечено"));

  if (item.has_photo) {
    const img = el("img", "card-photo");
    img.alt = item.title;
    viewEl.appendChild(img);
    apiBlob(`/api/items/${item.id}/photo`)
      .then((url) => { img.src = url; })
      .catch(() => { img.remove(); });
  }

  const blocks = [];
  if (item.description) blocks.push(item.description);
  if (item.hint) blocks.push("💡 " + item.hint);
  if (item.tips) blocks.push("📌 Советы: " + item.tips);
  if (!blocks.length && !item.has_photo) blocks.push("Описание пока не добавлено.");
  for (const text of blocks) {
    viewEl.appendChild(el("p", "card-block", text));
  }

  const btn = el("button", "btn", item.done ? "Снять отметку" : "Отметить");
  btn.addEventListener("click", async () => {
    const res = await api(`/api/items/${item.id}/toggle`, { method: "POST" });
    item.done = res.done;
    renderItem();
  });
  viewEl.appendChild(btn);
}

backBtn.addEventListener("click", () => {
  if (state.back) state.back().catch(showError);
});

async function main() {
  try {
    await showCategories();
  } catch (e) {
    showError("Не удалось загрузить чек-лист. Открой приложение через кнопку в боте.");
  }
}

main();
