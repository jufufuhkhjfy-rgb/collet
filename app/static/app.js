const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
let settings = {};
let options = {};
let stats = { series: [], posted: 0, failed: 0 };
let status = null;
let edition = null;
// 販売モードで使える機能。読み込む前は全部使える扱いにしておく
let unlocked = { app: true, ai: true, cards: true, room: true, bulk: true };

async function api(path, method = "GET", body) {
  const res = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    // サーバーが理由を日本語で返していれば、それを見せる
    const detail = await res.json().then((d) => d.detail).catch(() => null);
    throw new Error(typeof detail === "string" ? detail : `サーバーがエラーを返しました（${res.status}）`);
  }
  return res.json();
}

// action を渡すと、お知らせの横にボタン（「元に戻す」など）を出して、少し長めに表示する
function toast(text, action) {
  const box = $("toast");
  box.replaceChildren(document.createTextNode(text));
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "toast-action";
    b.textContent = action.label;
    b.onclick = () => {
      box.hidden = true;
      action.onClick();
    };
    box.append(b);
  }
  box.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (box.hidden = true), action ? 7000 : 2400);
}

// 「ROOMで開く」を押した候補を投稿済みに移す。押し間違えたときは「元に戻す」で投稿待ちに戻せる
async function markOpened(ids) {
  for (const id of ids) await api(`/api/queue/${id}/posted?opened=true`, "POST").catch(() => {});
  await refreshAll();
  toast(ids.length > 1 ? `${ids.length}件を投稿済みにしました` : "投稿済みにしました", {
    label: "元に戻す",
    onClick: async () => {
      for (const id of ids) await api(`/api/queue/${id}/reopen`, "POST").catch(() => {});
      await refreshAll();
      toast("投稿待ちに戻しました");
    },
  });
}

function el(tag, attrs = {}, text) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
  if (text !== undefined) node.textContent = text;
  return node;
}

const fmtTime = (iso) => iso.replace("T", " ").slice(5, 16).replace("-", "/");
const fmtDay = (iso) => `${Number(iso.slice(5, 7))}/${Number(iso.slice(8, 10))}`;
const fmtDate = (iso) => `${iso.slice(0, 4)}年${Number(iso.slice(5, 7))}月${Number(iso.slice(8, 10))}日`;

/* ---------- 画面の切り替え ---------- */
function showView() {
  const view = (location.hash || "#home").slice(1);
  let name = ["home", "queue", "settings", "history", "plan"].includes(view) ? view : "home";
  if (name === "plan" && !edition?.owner) name = "home";
  // 全部有料（C）で利用キーがないときは、利用キーを入れる画面だけを出す
  if (!unlocked.app && name !== "plan") name = "lock";
  $("top-actions").hidden = !unlocked.app;
  for (const v of document.querySelectorAll(".view")) v.hidden = v.id !== `view-${name}`;
  for (const a of document.querySelectorAll(".nav a")) {
    if (a.dataset.view === name) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  if (name === "home") drawChart();
}

function greet() {
  const h = new Date().getHours();
  $("greeting").textContent = h < 11 ? "おはようございます" : h < 18 ? "こんにちは" : "こんばんは";
}

/* ---------- 設定 ---------- */
async function save(changes, done = "保存しました") {
  try {
    settings = await api("/api/settings", "PUT", changes);
    renderSettings();
    refreshStatus();
    toast(done);
  } catch (e) {
    toast(e.message);
  }
}

function renderSettings() {
  const presets = $("presets");
  presets.innerHTML = "";
  for (const [key, p] of Object.entries(options.presets)) {
    const b = el("button", { type: "button", class: "seg", role: "radio", "aria-checked": String(settings.preset === key) });
    b.append(el("strong", {}, p.label), el("span", {}, `${p.interval_minutes / 60}時間ごと・1日${p.daily_limit}件まで`));
    b.onclick = () => save({ preset: key }, `ペースを「${p.label}」にしました`);
    presets.append(b);
  }

  const genres = $("genres");
  genres.innerHTML = "";
  for (const group of options.genre_groups) {
    const wrap = el("div", { class: "genre-group" });
    wrap.append(el("span", { class: "label" }, group.name));
    const chips = el("div", { class: "chips" });
    for (const g of group.genres) {
      const on = settings.genres.includes(g.id);
      const c = el("button", { type: "button", class: "chip", "aria-pressed": String(on) }, g.name);
      c.onclick = () => {
        const next = on ? settings.genres.filter((x) => x !== g.id) : [...settings.genres, g.id];
        save({ genres: next }, on ? `「${g.name}」を外しました` : `「${g.name}」を追加しました`);
      };
      chips.append(c);
    }
    wrap.append(chips);
    genres.append(wrap);
  }

  for (const id of ["active_start", "active_end", "min_review_average", "min_review_count", "max_price"]) {
    if (document.activeElement !== $(id)) $(id).value = settings[id];
  }
  $("ai_images").checked = !!settings.ai_images;
}

/* ---------- 状態とKPI ---------- */
async function refreshStatus() {
  status = await api("/api/status");
  const pill = $("state-pill");
  pill.textContent = status.enabled ? "自動で候補を集め中" : "停止中";
  pill.classList.toggle("on", status.enabled);
  $("next-run").textContent = status.next_run ? `次の候補集め ${fmtTime(status.next_run)}` : "";

  const t = $("toggle");
  t.textContent = status.enabled ? "自動の候補集めを止める" : "自動で候補を集める";
  t.classList.toggle("stop", status.enabled);
  t.dataset.enabled = String(status.enabled);

  // 必須の準備が足りないときだけ「はじめる前に」を出す
  const missingRequired = status.setup.filter((s) => s.required && !s.ok);
  $("setup-card").hidden = !missingRequired.length;
  const done = status.setup.filter((s) => s.ok).length;
  $("setup-count").textContent = `${done} / ${status.setup.length} 完了`;
  const list = $("setup-list");
  list.innerHTML = "";
  for (const s of status.setup) list.append(el("li", { class: s.ok ? "done" : "" }, s.label));

  $("kpi-today").textContent = status.posted_today;
  $("kpi-limit").textContent = ` / ${status.daily_limit}件`;
  const left = Math.max(status.daily_limit - status.posted_today, 0);
  $("kpi-today-note").textContent = left ? `目標まであと${left}件` : "今日の目標を達成しました";
  $("kpi-pending").textContent = status.pending;
  $("kpi-pace").textContent = status.pending ? "押すと投稿画面へ" : status.next_run ? `次の候補 ${status.next_run.slice(11, 16)}` : "候補はまだありません";

  $("images-unavailable").hidden = status.card_available;
  $("ai_images").disabled = !status.card_available || !unlocked.cards;

  const badge = $("nav-badge");
  badge.textContent = status.pending;
  badge.hidden = !status.pending;
}

/* ---------- 今日の投稿 ---------- */
const SOURCE_LABEL = { ai: "Claudeで作成", local: "PCのAIで作成", template: "テンプレートで作成" };
// 拡張機能「Collet ROOM アシスト」が入っていると、ページ読み込み前に印を付けてくれる
const hasExt = () => document.documentElement.dataset.colletExt === "1";

function renderQueueTools(count) {
  const ext = hasExt();
  $("queue-help").textContent = ext
    ? "「ROOMで開く」を押すと、ROOMの投稿画面に紹介文が自動で入ります。内容を確認して「完了」を押せば、ここにも自動で記録されます。"
    : "「コピーしてROOMで開く」を押すと、紹介文がコピーされてROOMの投稿画面が開きます。貼り付けて「完了」を押したら、ここに戻って「投稿した」を押してください。";
  const extEl = $("ext-status");
  extEl.textContent = ext ? "拡張機能「Collet ROOM アシスト」に接続済み" : "拡張機能を入れると、紹介文の入力と記録が自動になります（README を参照）";
  extEl.classList.toggle("on", ext);
  const openAll = $("open-all");
  openAll.hidden = !ext || !count;
  openAll.textContent = unlocked.bulk ? `まとめてROOMで開く（${count}件）` : "まとめてROOMで開く（有料プラン）";
}

// その機能が入っている、いちばん安いプラン
function planFor(feature) {
  const list = Object.values(edition.plans)
    .filter((p) => p.enabled && p.features.includes(feature))
    .sort((a, b) => a.price - b.price);
  return list[0];
}

// 有料プランの機能を押したとき
function lockedToast(feature) {
  const p = planFor(feature);
  const name = edition.features[feature];
  toast(p ? `「${name}」は${p.label}プランの機能です` : `「${name}」は、いまは使えません`, {
    label: "利用キーを入れる",
    onClick: () => (location.hash = "#settings"),
  });
}

let queueIds = [];

async function refreshQueue() {
  const list = await api("/api/queue");
  queueIds = list.map((q) => q.id);
  renderQueueTools(list.length);
  const box = $("queue-list");
  // 編集中の紹介文を消さないよう、入力中は描き直さない
  if (box.contains(document.activeElement) && document.activeElement.tagName === "TEXTAREA") return;
  box.innerHTML = "";
  if (!list.length) {
    const empty = el("div", { class: "card queue-empty" });
    empty.append(
      el("img", { src: "/static/img/leaf-blue.png", alt: "" }),
      el("h2", {}, "投稿待ちはありません"),
      el("p", { class: "muted" }, "右上の「今すぐ候補を集める」で、選んだジャンルから商品を用意できます。"),
    );
    box.append(empty);
    return;
  }
  for (const q of list) box.append(queueCard(q));
}

function queueCard(q) {
  const it = q.item;
  const card = el("article", { class: "card qcard" });

  const top = el("div", { class: "q-top" });
  top.append(el("img", { src: it.image_url || "/static/icon.png", alt: "", loading: "lazy" }));
  const info = el("div");
  info.append(
    el("a", { class: "q-name", href: it.url, target: "_blank", rel: "noopener" }, it.name),
    el("p", { class: "q-meta" }, `¥${Number(it.price).toLocaleString()} · ★${Number(it.review_average).toFixed(1)}（${Number(it.review_count).toLocaleString()}件） · ${it.shop}`),
  );
  top.append(info);

  const label = el("div", { class: "q-label" });
  const id = `comment-${q.id}`;
  const copy = el("button", { type: "button", class: "copy-btn" }, "コピー");
  const right = el("span", { class: "q-label-right" });
  right.append(el("span", { class: "q-source" }, SOURCE_LABEL[q.comment_source] || ""), copy);
  label.append(el("label", { class: "label", for: id }, "紹介文"), right);
  const area = el("textarea", { id });
  copy.onclick = async () => {
    try {
      await navigator.clipboard.writeText(area.value);
      copy.textContent = "コピーしました";
      copy.classList.add("done");
      setTimeout(() => { copy.textContent = "コピー"; copy.classList.remove("done"); }, 2000);
    } catch {
      area.select();
      toast("自動でコピーできませんでした。選択したので Ctrl+C を押してください");
    }
  };
  area.value = q.comment;
  area.addEventListener("change", async () => {
    try {
      await api(`/api/queue/${q.id}`, "PUT", { comment: area.value });
      toast("紹介文を保存しました");
    } catch (e) {
      toast(e.message);
    }
  });

  const hint = el("p", { class: "q-hint" });
  hint.hidden = true;
  const actions = el("div", { class: "q-actions" });
  const open = el("button", { type: "button", class: "btn brand small" }, hasExt() ? "ROOMで開く" : "コピーしてROOMで開く");
  const posted = el("button", { type: "button", class: "btn ghost small" }, "投稿した");
  const skip = el("button", { type: "button", class: "btn text small" }, "見送る");

  open.onclick = async () => {
    // 拡張機能が読むのは保存済みの紹介文なので、開く前に保存しておく
    if (area.value !== q.comment) {
      await api(`/api/queue/${q.id}`, "PUT", { comment: area.value }).catch(() => {});
      q.comment = area.value;
    }
    let copied = true;
    try {
      await navigator.clipboard.writeText(area.value);
    } catch {
      copied = false;
    }
    window.open(q.post_url, "_blank", "noopener");
    if (!copied && !hasExt()) {
      // 紹介文を渡す手段がないので、この候補は残して、手でコピーしてもらう
      area.select();
      hint.textContent = "自動でコピーできませんでした。紹介文を選択したので Ctrl+C でコピーしてください。";
      hint.hidden = false;
      return;
    }
    await markOpened([q.id]);
  };
  posted.onclick = () => finish(q.id, "posted", "投稿済みにしました");
  skip.onclick = () => finish(q.id, "skip", "見送りました");

  actions.append(open, posted, skip);
  card.append(top, imageBlock(q), label, area, actions, hint);
  return card;
}

function imageBlock(q) {
  const wrap = el("div", { class: "q-images" });
  if (q.images_working) {
    wrap.append(el("p", { class: "q-images-note" }, "画像を作っています…"));
    setTimeout(refreshQueue, 3000);
    return wrap;
  }
  if (q.images_error) wrap.append(el("p", { class: "q-images-note" }, q.images_error));
  if (q.images.length) {
    const row = el("div", { class: "q-thumbs" });
    q.images.forEach((src, i) => {
      const ext = src.split(".").pop();
      const a = el("a", { href: src, download: `collet-${q.id}-${i + 1}.${ext}`, title: "押すと保存します" });
      a.append(el("img", { src: `${src}?v=${encodeURIComponent(q.updated_at)}`, alt: `画像 ${i + 1}` }));
      row.append(a);
    });
    wrap.append(row, el("p", { class: "q-images-note" }, "画像を押すと保存できます。"));
  }

  const buttons = el("div", { class: "q-actions" });
  const makeButton = (kind, text, wait) => {
    const feature = kind === "card" ? "cards" : "room";
    const b = el("button", { type: "button", class: "btn text small" }, unlocked[feature] ? text : `${text}（有料プラン）`);
    b.onclick = async () => {
      if (!unlocked[feature]) return lockedToast(feature);
      b.disabled = true;
      try {
        await api(`/api/queue/${q.id}/images?kind=${kind}`, "POST");
        toast(`画像を作り始めました。${wait}で表示されます`);
        setTimeout(refreshQueue, 800);
      } catch (e) {
        toast(e.message);
        b.disabled = false;
      }
    };
    return b;
  };
  if (status?.card_available) buttons.append(makeButton("card", "紹介カードを作る", "数秒"));
  if (status?.room_available) buttons.append(makeButton("room", "部屋に置いた画像を作る", "1分くらい"));
  if (buttons.children.length) wrap.append(buttons);
  return wrap;
}

async function finish(id, action, done) {
  try {
    await api(`/api/queue/${id}/${action}`, "POST");
    toast(done);
    await refreshAll();
  } catch (e) {
    toast(e.message);
  }
}

/* ---------- グラフ ---------- */
async function refreshStats() {
  stats = await api(`/api/stats?days=${$("range").value}`);
  const week = stats.series.slice(-7);
  const weekPosted = week.reduce((s, d) => s + d.posted, 0);
  const weekFailed = week.reduce((s, d) => s + d.failed, 0);
  $("kpi-week").textContent = weekPosted;
  $("kpi-week-note").textContent = `1日平均 ${(weekPosted / 7).toFixed(1)}件`;
  $("kpi-failed").textContent = weekFailed;
  $("kpi-failed-note").textContent = weekFailed ? "お知らせで理由を確認できます" : "問題はありません";
  drawChart();
  renderChartTable();
}

function niceMax(v) {
  if (v <= 4) return 4;
  const step = Math.ceil(v / 4);
  return step * 4;
}

function drawChart() {
  const box = $("chart");
  if (!box.offsetWidth || !stats.series.length) return;
  const W = box.clientWidth, H = box.clientHeight;
  const pad = { l: 32, r: 12, t: 12, b: 28 };
  const data = stats.series;
  const max = niceMax(Math.max(...data.map((d) => d.posted)));
  const x = (i) => pad.l + (data.length === 1 ? 0 : (i * (W - pad.l - pad.r)) / (data.length - 1));
  const y = (v) => pad.t + (1 - v / max) * (H - pad.t - pad.b);

  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
  const mk = (tag, attrs, parent = svg) => {
    const n = document.createElementNS(SVG, tag);
    for (const [k, v] of Object.entries(attrs)) n.setAttribute(k, v);
    parent.append(n);
    return n;
  };

  const grid = mk("g", { class: "grid" });
  const axis = mk("g", { class: "axis" });
  for (let i = 0; i <= 4; i++) {
    const v = (max / 4) * i;
    mk("line", { x1: pad.l, x2: W - pad.r, y1: y(v), y2: y(v) }, grid);
    mk("text", { x: pad.l - 8, y: y(v) + 4, "text-anchor": "end" }, axis).textContent = v;
  }
  const every = Math.ceil(data.length / 8);
  data.forEach((d, i) => {
    if (i % every === 0 || i === data.length - 1) {
      mk("text", { x: x(i), y: H - 6, "text-anchor": "middle" }, axis).textContent = fmtDay(d.date);
    }
  });

  const pts = data.map((d, i) => `${x(i)},${y(d.posted)}`);
  mk("path", { class: "area", d: `M${x(0)},${y(0)} L${pts.join(" L")} L${x(data.length - 1)},${y(0)} Z` });
  mk("path", { class: "line", d: `M${pts.join(" L")}` });
  data.forEach((d, i) => {
    if (data.length <= 14 || i === data.length - 1) {
      mk("circle", { class: i === data.length - 1 ? "dot end" : "dot", cx: x(i), cy: y(d.posted), r: 4.5 });
    }
  });

  const cross = mk("line", { class: "cross", y1: pad.t, y2: H - pad.b, x1: 0, x2: 0, visibility: "hidden" });
  const tip = $("tooltip");
  const hit = mk("rect", { class: "hit", x: pad.l - 10, y: 0, width: W - pad.l - pad.r + 20, height: H });
  hit.addEventListener("pointermove", (e) => {
    const r = svg.getBoundingClientRect();
    const px = ((e.clientX - r.left) / r.width) * W;
    const i = Math.max(0, Math.min(data.length - 1, Math.round(((px - pad.l) / (W - pad.l - pad.r)) * (data.length - 1))));
    cross.setAttribute("x1", x(i));
    cross.setAttribute("x2", x(i));
    cross.setAttribute("visibility", "visible");
    const d = data[i];
    tip.textContent = `${fmtDay(d.date)}　投稿 ${d.posted}件${d.failed ? `・失敗 ${d.failed}件` : ""}`;
    tip.hidden = false;
    const tx = r.left + (x(i) / W) * r.width;
    tip.style.left = `${Math.min(tx + 12, innerWidth - tip.offsetWidth - 8)}px`;
    tip.style.top = `${r.top + (y(d.posted) / H) * r.height - 40}px`;
  });
  hit.addEventListener("pointerleave", () => {
    tip.hidden = true;
    cross.setAttribute("visibility", "hidden");
  });

  box.replaceChildren(svg);
}

function renderChartTable() {
  const t = $("chart-table");
  t.innerHTML = "";
  const head = el("tr");
  head.append(el("th", {}, "日付"), el("th", {}, "投稿"), el("th", {}, "失敗・見送り"));
  t.append(head);
  for (const d of stats.series) {
    const tr = el("tr");
    tr.append(el("td", {}, fmtDay(d.date)), el("td", { class: "num" }, `${d.posted}件`), el("td", { class: "num" }, `${d.failed}件`));
    t.append(tr);
  }
}

/* ---------- 投稿一覧とお知らせ ---------- */
const STATUS_LABEL = { posted: "投稿済み", failed: "失敗", skipped: "見送り" };

function renderPostsTable(table, list, withComment) {
  table.innerHTML = "";
  if (!list.length) {
    const tr = el("tr");
    tr.append(el("td", { class: "empty", colspan: "4" }, "まだ投稿はありません。「今日の投稿」から投稿すると、ここに記録されます。"));
    table.append(tr);
    return;
  }
  const head = el("tr");
  head.append(el("th", {}, "商品"), el("th", {}, "価格"), el("th", {}, "状態"), el("th", {}, "日時"));
  table.append(head);
  for (const p of list) {
    const tr = el("tr");
    const item = el("div", { class: "item" });
    item.append(el("img", { src: p.image_url || "/static/icon.png", alt: "", loading: "lazy" }));
    const name = el("span", {}, p.item_name);
    if (withComment) name.title = p.comment || p.error || "";
    item.append(name);
    const tdItem = el("td");
    tdItem.append(item);
    const tdStatus = el("td");
    tdStatus.append(el("span", { class: `badge ${p.status}` }, STATUS_LABEL[p.status] || p.status));
    tr.append(tdItem, el("td", { class: "num" }, `¥${Number(p.price).toLocaleString()}`), tdStatus, el("td", { class: "num" }, fmtTime(p.created_at)));
    table.append(tr);
  }
}

function renderNotices(list) {
  const box = $("notices");
  box.innerHTML = "";
  const items = [];
  if (status?.last_message?.at) {
    items.push({ warn: !status.last_message.ok, title: status.last_message.text, sub: "最後の実行結果", at: status.last_message.at });
  }
  for (const p of list.filter((p) => p.status !== "posted").slice(0, 4)) {
    items.push({ warn: true, title: `${STATUS_LABEL[p.status]}: ${p.item_name.slice(0, 28)}…`, sub: p.error || "", at: p.created_at });
  }
  if (!items.length) items.push({ warn: false, title: "お知らせはありません", sub: "投稿の結果やエラーがここに出ます。", at: "" });
  for (const n of items.slice(0, 5)) {
    const li = el("li", { class: n.warn ? "notice warn" : "notice" });
    const body = el("div");
    body.append(el("p", {}, n.title));
    if (n.sub) body.append(el("p", { class: "sub" }, n.sub));
    li.append(body, el("time", {}, n.at ? n.at.slice(11, 16) : ""));
    box.append(li);
  }
}

async function refreshPosts() {
  const list = await api("/api/posts?limit=50");
  renderPostsTable($("recent-table"), list.slice(0, 5), false);
  renderPostsTable($("history-table"), list, true);
  renderNotices(list);
}

/* ---------- 操作 ---------- */
function bindInputs() {
  for (const id of ["active_start", "active_end", "min_review_count", "max_price"]) {
    $(id).addEventListener("change", (e) => save({ [id]: parseInt(e.target.value || "0", 10) }));
  }
  $("min_review_average").addEventListener("change", (e) => save({ min_review_average: parseFloat(e.target.value || "0") }));
  $("ai_images").addEventListener("change", (e) =>
    save({ ai_images: e.target.checked }, e.target.checked ? "次の候補から紹介カードも作ります" : "紹介カードを自動で作らないようにしました"));
  $("range").addEventListener("change", refreshStats);

  $("toggle").onclick = async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    const running = btn.dataset.enabled === "true";
    try {
      await api(running ? "/api/stop" : "/api/start", "POST");
      toast(running ? "自動の候補集めを止めました" : "自動で候補を集めます。届いたら「今日の投稿」に並びます");
      await refreshStatus();
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  };

  $("run-now").onclick = async (e) => {
    const btn = e.currentTarget;
    const todo = status?.setup.filter((s) => s.required && !s.ok) ?? [];
    if (todo.length) {
      toast(`まだ候補を集められません。先に「${todo[0].label}」をしてください`);
      location.hash = "#home";
      return;
    }
    btn.disabled = true;
    try {
      await api("/api/run-now", "POST");
      toast("候補を集めています…");
      setTimeout(async () => {
        await refreshAll();
        if (status.pending) location.hash = "#queue";
        toast(status.last_message.text);
      }, 4000);
    } catch (err) {
      toast(err.message);
    }
    setTimeout(() => (btn.disabled = false), 5000);
  };

  // 終了は2回押し。1回目で確認の表示に変わる
  $("quit").onclick = async (e) => {
    const btn = e.currentTarget;
    if (!btn.classList.contains("armed")) {
      btn.classList.add("armed");
      btn.textContent = "もう一度押すと終了";
      setTimeout(() => { btn.classList.remove("armed"); btn.textContent = "Collet を終了"; }, 4000);
      return;
    }
    try { await api("/api/quit", "POST"); } catch {}
    document.body.innerHTML = '<p style="padding:48px;font:15px/24px sans-serif">Collet を終了しました。このウィンドウは閉じて大丈夫です。もう一度使うときは、デスクトップの Collet をダブルクリックしてください。</p>';
  };

  bindEditionInputs();

  let openAllIds = [];
  $("open-all").onclick = () => {
    if (!unlocked.bulk) return lockedToast("bulk");
    // 拡張機能に頼んでタブを開いてもらう（ブラウザは1回のクリックで1つしか窓を開けないため）
    openAllIds = [...queueIds];
    window.postMessage({ type: "collet-open-all" }, "*");
  };
  window.addEventListener("message", (e) => {
    if (e.source !== window || e.data?.type !== "collet-open-all-result") return;
    if (e.data.error) return toast(`開けませんでした: ${e.data.error}`);
    markOpened(openAllIds);
  });
  // ROOMで投稿して戻ってきたら、すぐに最新の状態にする
  window.addEventListener("focus", refreshAll);

  let resizeTimer;
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(drawChart, 150);
  });
}

/* ---------- 販売モードと利用キー ---------- */
const PLAN_TODO = {
  A: [
    "今の準備のまま、GitHub に無料で公開できます",
    "応援を受け付けるなら、応援ページを作ってURLを入れる（未成年は保護者の同意が必要）",
  ],
  B: [
    "保護者の名前で、決済サービス（Stripe など）に申し込む",
    "プランごとに Stripe の決済用リンクを作って、下に入れる",
    "販売ページに「特定商取引法に基づく表記」を載せて、GitHub で公開する",
    "Stripe の制限付きキーを .env に入れて、「利用キーを自動で渡す」を動かす",
  ],
};
PLAN_TODO.C = [...PLAN_TODO.B, "GitHub に無料で公開しない（無料で公開すると、確認を外して使えてしまうため）"];

async function refreshEdition() {
  edition = await api("/api/edition");
  const wasApp = unlocked.app;
  unlocked = edition.unlocked;
  renderEdition();
  // 使える・使えないが変わったときだけ、画面を出し直す
  if (wasApp !== unlocked.app || location.hash === "#plan") showView();
}

const yen = (n) => `${Number(n).toLocaleString()}円`;
const featureNames = (list) => list.map((f) => edition.features[f]).join("・");

// 使う人に見せる、プランの一覧（買うリンク付き）
function renderOffers(box) {
  const e = edition;
  const current = e.license.ok ? e.license.plan : null;
  box.innerHTML = "";
  for (const [pid, p] of Object.entries(e.plans)) {
    if (!p.enabled && pid !== current) continue;
    const card = el("div", { class: pid === current ? "offer current" : "offer" });
    const head = el("div", { class: "offer-head" });
    head.append(el("strong", {}, `${p.label}プラン`), el("span", { class: "offer-price" }, `月${yen(p.price)}`));
    card.append(head, el("p", { class: "small muted" }, featureNames(p.features)));
    if (pid === current) card.append(el("span", { class: "offer-badge" }, "利用中"));
    else if (p.shop_url) card.append(el("a", { class: "link", href: p.shop_url, target: "_blank", rel: "noopener" }, "このプランを買う →"));
    box.append(card);
  }
}

function renderEdition() {
  const e = edition;
  $("nav-plan").hidden = !e.owner;

  const support = $("support-link");
  support.hidden = !(e.mode === "A" && e.support_url);
  if (e.support_url) support.href = e.support_url;

  // 投稿設定の「有料プラン」
  const lic = e.license;
  $("license-card").hidden = e.mode === "A";
  const pill = $("license-pill");
  pill.textContent = lic.ok ? `${e.plans[lic.plan].label}プラン` : "利用キーなし";
  pill.classList.toggle("on", lic.ok);
  let text;
  if (lic.ok) text = `${e.plans[lic.plan].label}プランの利用キーが入っています。${fmtDate(lic.until)}まで使えます。`;
  else if (e.owner && !e.preview) text = "作者のPCなので、利用キーがなくてもすべての機能が使えます。";
  else if (lic.reason) text = lic.reason;
  else if (e.mode === "B") text = "下のプランの機能は、購入すると届く利用キーを入れると使えます。";
  else text = "Collet は、購入すると届く利用キーを入れると使えます。";
  $("license-text").textContent = text;
  $("license-remove").hidden = !lic.has_key;
  renderOffers($("license-plans"));

  // 利用キーを入れる画面（C）
  $("lock-text").textContent = lic.reason || "Collet は有料プランで使えます。購入すると届く利用キーを、下に貼り付けてください。";
  renderOffers($("lock-plans"));
  $("lock-owner").hidden = !(e.owner && e.preview);

  if (e.owner) renderPlan();
}

// 販売モードの画面の、プランごとの設定
function planCard(pid, p) {
  const e = edition;
  const card = el("article", { class: p.enabled ? "card plan-card" : "card plan-card off" });
  const head = el("div", { class: "card-head" });
  head.append(el("h2", {}, `${p.label}プラン`));
  const sell = el("label", { class: "check" });
  const sellBox = el("input", { type: "checkbox" });
  sellBox.checked = p.enabled;
  sellBox.onchange = () => savePlan(pid, { enabled: sellBox.checked }, sellBox.checked ? `${p.label}プランを売るようにしました` : `${p.label}プランを売らないようにしました`);
  sell.append(sellBox, el("span", {}, "このプランを売る"));
  head.append(sell);

  const fields = el("div", { class: "field-grid" });
  const nameField = el("label", { class: "field" });
  const name = el("input", { class: "text-input", type: "text", maxlength: "20", value: p.label });
  name.onchange = () => savePlan(pid, { label: name.value }, "プランの名前を保存しました");
  nameField.append(el("span", { class: "label" }, "プランの名前"), name);
  const priceField = el("label", { class: "field" });
  const price = el("input", { type: "number", min: "0", step: "10", inputmode: "numeric", value: String(p.price) });
  price.onchange = () => savePlan(pid, { price: parseInt(price.value || "0", 10) }, "月額を保存しました");
  const priceRow = el("span", { class: "inline" });
  priceRow.append("月 ", price, " 円");
  priceField.append(el("span", { class: "label" }, "月額"), priceRow);
  fields.append(nameField, priceField);

  const shopField = el("label", { class: "field" });
  const shop = el("input", { class: "text-input", type: "url", placeholder: "https://buy.stripe.com/...", autocomplete: "off", value: p.shop_url });
  shop.onchange = () => savePlan(pid, { shop_url: shop.value }, "支払いのリンクを保存しました");
  shopField.append(el("span", { class: "label" }, "支払いのリンク（Stripe の決済用リンク）"), shop);

  const chips = el("div", { class: "chips" });
  for (const [key, label] of Object.entries(e.features)) {
    const on = p.features.includes(key);
    const c = el("button", { type: "button", class: "chip", "aria-pressed": String(on) }, label);
    c.onclick = () => {
      const next = on ? p.features.filter((x) => x !== key) : [...p.features, key];
      savePlan(pid, { features: next }, on ? `「${label}」を外しました` : `「${label}」を入れました`);
    };
    chips.append(c);
  }
  const featField = el("div", { class: "field" });
  featField.append(el("span", { class: "label" }, "このプランで使える機能"), chips);

  card.append(head, fields, shopField, featField);
  return card;
}

function savePlan(id, changes, done) {
  return saveEdition({ plan: { id, ...changes } }, done);
}

function renderPlan() {
  const e = edition;
  const box = $("plan-modes");
  box.innerHTML = "";
  for (const [key, m] of Object.entries(e.modes)) {
    const b = el("button", { type: "button", class: "seg", role: "radio", "aria-checked": String(e.mode === key) });
    b.append(el("span", { class: "plan-letter" }, key), el("strong", {}, m.label), el("span", {}, m.summary));
    b.onclick = () => saveEdition({ mode: key }, `「${key} ${m.label}」にしました`);
    box.append(b);
  }
  $("plan-preview").checked = e.preview;

  const todo = $("plan-todo");
  todo.innerHTML = "";
  for (const t of PLAN_TODO[e.mode]) todo.append(el("li", {}, t));

  const paidMode = e.mode !== "A";
  $("plan-support-card").hidden = paidMode;
  if (document.activeElement !== $("plan-support")) $("plan-support").value = e.support_url;
  $("plan-free-card").hidden = e.mode !== "B";
  const free = $("plan-free");
  free.innerHTML = "";
  const freeList = ["候補集め・テンプレートの紹介文・ROOMで開く", ...Object.keys(e.features).filter((f) => !e.paid_features.includes(f)).map((f) => e.features[f])];
  for (const t of freeList) free.append(el("li", {}, t));

  // 入力中のプランは描き直さない（書いている途中の文字が消えないように）
  const cards = $("plan-cards");
  cards.hidden = !paidMode;
  if (!cards.contains(document.activeElement)) {
    cards.innerHTML = "";
    for (const [pid, p] of Object.entries(e.plans)) cards.append(planCard(pid, p));
  }

  // 利用キーを自動で渡す
  $("plan-auto-card").hidden = !paidMode;
  if (document.activeElement !== $("plan-site")) $("plan-site").value = e.site_url;
  $("thanks-url").textContent = e.site_url
    ? `${e.site_url}/thanks.html?session_id={CHECKOUT_SESSION_ID}`
    : "（先に、上の「販売ページのURL」を入れてください）";
  $("thanks-copy").hidden = !e.site_url;
  const st = e.stripe || {};
  const autoPill = $("auto-pill");
  autoPill.textContent = !st.configured ? "未設定" : st.ok === false ? "うまくいっていません" : "動いています";
  autoPill.classList.toggle("on", !!st.configured && st.ok !== false);
  const at = st.at ? `（${st.at.slice(11, 16)} に確認）` : "";
  $("auto-status").textContent = st.configured ? `${st.text}${at}` : "";
  $("auto-status").classList.toggle("warn-text", st.ok === false);
  $("auto-help").textContent = st.configured
    ? ""
    : `Stripe で「制限付きキー」を作り、collet フォルダの .env に STRIPE_API_KEY=（キー）と書き込むと動き始めます。キーに付ける権限：${st.permissions || ""}`;
  $("auto-check").disabled = !st.configured;

  const planSelect = $("key-plan");
  const chosen = planSelect.value;
  planSelect.innerHTML = "";
  for (const [pid, p] of Object.entries(e.plans)) planSelect.append(el("option", { value: pid }, `${p.label}プラン`));
  if (chosen) planSelect.value = chosen;

  $("plan-keys-card").hidden = !paidMode;
  $("plan-nokey").hidden = e.can_issue;
  $("key-issue").disabled = !e.can_issue;
  if (paidMode) refreshKeys().catch(() => {});
}

async function saveEdition(changes, done = "保存しました") {
  try {
    await api("/api/edition", "PUT", changes);
    await refreshAll();
    toast(done);
  } catch (e) {
    toast(e.message);
  }
}

async function copyKey(key) {
  try {
    await navigator.clipboard.writeText(key);
    return true;
  } catch {
    window.prompt("自動でコピーできませんでした。この利用キーを選んで Ctrl+C でコピーしてください", key);
    return false;
  }
}

async function refreshKeys() {
  const list = await api("/api/edition/keys");
  const t = $("key-table");
  t.innerHTML = "";
  if (!list.length) {
    const tr = el("tr");
    tr.append(el("td", { class: "empty", colspan: "4" }, "まだ発行していません。"));
    t.append(tr);
    return;
  }
  const head = el("tr");
  head.append(el("th", {}, "メモ"), el("th", {}, "プラン"), el("th", {}, "期限"), el("th", {}, "利用キー"));
  t.append(head);
  const today = new Date().toISOString().slice(0, 10);
  for (const k of list.slice(0, 30)) {
    const tr = el("tr", { class: k.until < today ? "expired" : "" });
    const tdKey = el("td");
    const row = el("span", { class: "q-label-right" });
    const copy = el("button", { type: "button", class: "copy-btn" }, "コピー");
    copy.onclick = async () => {
      if (await copyKey(k.key)) toast("利用キーをコピーしました");
    };
    row.append(el("span", { class: "key-code" }, k.key), copy);
    tdKey.append(row);
    const planLabel = edition.plans[k.plan || "standard"]?.label || "";
    const memo = `${k.memo || "（メモなし）"}${k.auto ? "（自動）" : ""}`;
    tr.append(el("td", {}, memo), el("td", {}, planLabel), el("td", { class: "num" }, `${fmtDate(k.until)}まで`), tdKey);
    t.append(tr);
  }
}

async function submitKey(input) {
  const key = input.value.trim();
  if (!key) return toast("利用キーを貼り付けてください");
  try {
    const r = await api("/api/license", "POST", { key });
    input.value = "";
    await refreshAll();
    toast(`利用キーを入れました。${fmtDate(r.until)}まで使えます`);
  } catch (e) {
    toast(e.message);
  }
}

function bindEditionInputs() {
  $("plan-support").addEventListener("change", (e) => saveEdition({ support_url: e.target.value }, "応援ページのURLを保存しました"));
  $("plan-site").addEventListener("change", (e) => saveEdition({ site_url: e.target.value }, "販売ページのURLを保存しました"));
  $("thanks-copy").onclick = async () => {
    try {
      await navigator.clipboard.writeText($("thanks-url").textContent);
      toast("コピーしました。Stripe の決済用リンクの「支払い後」に貼り付けてください");
    } catch {
      window.prompt("自動でコピーできませんでした。選んで Ctrl+C でコピーしてください", $("thanks-url").textContent);
    }
  };
  $("auto-check").onclick = async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const st = await api("/api/edition/stripe-check", "POST");
      await refreshEdition();
      toast(st.text);
    } catch (err) {
      toast(err.message);
    } finally {
      btn.disabled = false;
    }
  };
  $("plan-preview").addEventListener("change", async (e) => {
    try {
      await api("/api/edition/preview", "POST", { on: e.target.checked });
      await refreshAll();
      toast(e.target.checked ? "使う人の見え方にしました" : "作者の見え方に戻しました");
    } catch (err) {
      toast(err.message);
    }
  });

  $("key-issue").onclick = async (ev) => {
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const k = await api("/api/edition/keys", "POST", { memo: $("key-memo").value, months: Number($("key-months").value), plan: $("key-plan").value });
      $("key-memo").value = "";
      await refreshKeys();
      const copied = await copyKey(k.key);
      toast(`利用キーを発行しました（${fmtDate(k.until)}まで）${copied ? "。コピー済みです" : ""}`);
    } catch (e) {
      toast(e.message);
    } finally {
      btn.disabled = !edition.can_issue;
    }
  };

  for (const [input, button] of [["license-key", "license-save"], ["lock-key", "lock-save"]]) {
    $(button).onclick = () => submitKey($(input));
    $(input).addEventListener("keydown", (e) => {
      if (e.key === "Enter") submitKey($(input));
    });
  }
  $("license-remove").onclick = async () => {
    try {
      await api("/api/license", "DELETE");
      await refreshAll();
      toast("利用キーを外しました");
    } catch (e) {
      toast(e.message);
    }
  };
}

async function refreshAll() {
  await refreshEdition();
  await refreshStatus();
  await Promise.all([refreshStats(), refreshPosts(), refreshQueue()]);
}

async function init() {
  greet();
  // メニューの切り替えは、データの読み込みに失敗しても動くよう先に用意する
  window.addEventListener("hashchange", showView);
  showView();

  // Collet の準備が終わる前に画面が開いても、つながるまでやり直す
  for (let i = 0; ; i++) {
    try {
      [options, settings] = await Promise.all([api("/api/options"), api("/api/settings")]);
      break;
    } catch (e) {
      if (i % 5 === 0) toast("Collet に接続しています…");
      await new Promise((r) => setTimeout(r, 1000));
    }
  }
  renderSettings();
  bindInputs();
  showView();
  await refreshAll().catch(() => {});
  setInterval(() => refreshAll().catch(() => {}), 15000);
}

init().catch((e) => {
  console.error(e);
  toast(`画面の準備中にエラーが起きました: ${e.message}`);
});
