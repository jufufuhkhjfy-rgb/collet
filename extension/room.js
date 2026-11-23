// 楽天ROOMの投稿画面で、Collet の紹介文を入力する。
// 「完了」を押すのは本人。押されたら Collet に投稿済みとして記録する。

const SUBMIT_TEXT = /完了|コレ！|投稿する/;
const PENDING_KEY = "colletPending";

function send(msg) {
  return new Promise((resolve) => chrome.runtime.sendMessage(msg, resolve));
}

function banner(text, warn = false) {
  let host = document.getElementById("collet-banner");
  if (!host) {
    host = document.createElement("div");
    host.id = "collet-banner";
    Object.assign(host.style, {
      position: "fixed", right: "16px", bottom: "16px", zIndex: "2147483647", maxWidth: "360px",
      padding: "12px 16px", borderRadius: "14px", font: "700 13px/20px system-ui, sans-serif",
      color: "#FFFFFF", boxShadow: "0 4px 16px rgba(0,0,0,.2)",
    });
    document.body.append(host);
  }
  host.style.background = warn ? "#C2553A" : "#095D7B";
  host.textContent = `Collet: ${text}`;
}

function visible(el) {
  return el.offsetParent !== null && !el.disabled && !el.readOnly;
}

function waitFor(find, timeout) {
  return new Promise((resolve) => {
    const found = find();
    if (found) return resolve(found);
    const obs = new MutationObserver(() => {
      const el = find();
      if (el) {
        obs.disconnect();
        resolve(el);
      }
    });
    obs.observe(document.body, { childList: true, subtree: true });
    setTimeout(() => {
      obs.disconnect();
      resolve(null);
    }, timeout);
  });
}

// React などの画面でも入力として認識されるように、本来の setter を通して値を入れる
function setValue(el, value) {
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, "value").set;
  setter.call(el, value);
  el.dispatchEvent(new Event("input", { bubbles: true }));
  el.dispatchEvent(new Event("change", { bubbles: true }));
}

async function markPosted(id) {
  await chrome.storage.local.remove(PENDING_KEY);
  const res = await send({ type: "posted", id });
  if (res?.ok) banner("投稿を記録しました。このタブは閉じて大丈夫です。");
  else banner("投稿は記録できませんでした。Collet の画面で「投稿した」を押してください。", true);
}

// 「完了」が押されたあと、投稿画面が閉じたら（入力欄が消えるか、別のページに移ったら）投稿成功とみなす
function watchSubmit(box, id) {
  document.addEventListener(
    "click",
    (e) => {
      const b = e.target.closest("button, a, input[type=submit]");
      if (!b || !SUBMIT_TEXT.test(b.textContent || b.value || "")) return;
      chrome.storage.local.set({ [PENDING_KEY]: { id, at: Date.now() } });
      const started = Date.now();
      const timer = setInterval(() => {
        if (!document.contains(box) || !location.pathname.startsWith("/mix")) {
          clearInterval(timer);
          markPosted(id);
        } else if (Date.now() - started > 15000) {
          clearInterval(timer); // 入力ミスなどで画面が閉じなかった。押し直しを待つ
        }
      }, 500);
    },
    true,
  );
}

async function main() {
  // 「完了」のあと別ページに移った場合は、移った先でここが動いて記録する
  const { [PENDING_KEY]: pending } = await chrome.storage.local.get(PENDING_KEY);
  const onPostPage = location.pathname.startsWith("/mix");
  if (pending && !onPostPage && Date.now() - pending.at < 60000) {
    await markPosted(pending.id);
    return;
  }

  const code = new URLSearchParams(location.search).get("itemcode");
  if (!onPostPage || !code) return;

  const res = await send({ type: "find", code });
  if (res?.error) return banner("Collet に接続できません。Collet を起動しているか確認してください。", true);
  if (!res?.entry) return; // Collet 以外から開いた投稿画面なので何もしない

  const box = await waitFor(() => [...document.querySelectorAll("textarea")].find(visible), 20000);
  if (!box) return banner("紹介文の入力欄が見つかりませんでした。Collet の画面からコピーして貼り付けてください。", true);

  if (!box.value.trim()) setValue(box, res.entry.comment);
  const attached = await attachImages(res.entry.images || []);
  // 画像はもうROOMの画面に入ったので、Collet 側のファイルは消してもらう
  if (attached) send({ type: "imagesUsed", id: res.entry.id });
  banner(`紹介文${attached ? `と画像${attached}枚` : ""}を入力しました。内容を確認して「完了」を押してください。`);
  watchSubmit(box, res.entry.id);
}

// ROOMの画像を追加する欄（ファイル選択）に、Collet の画像を入れる。入れた枚数を返す
async function attachImages(urls) {
  if (!urls.length) return 0;
  let input = document.querySelector('input[type="file"]');
  if (!input) {
    // 「画像を追加」のようなボタンを押すと欄が出る画面に備えて、一度だけ押してみる
    // ページを移動してしまわないよう、リンクは押さない
    const opener = [...document.querySelectorAll("button, [role=button]")]
      .find((el) => /画像|写真/.test(el.textContent || "") && /追加|選択|アップ/.test(el.textContent || ""));
    if (opener) opener.click();
    input = await waitFor(() => document.querySelector('input[type="file"]'), 3000);
  }
  if (!input) {
    banner("画像を追加する欄が見つかりませんでした。紹介文だけ入れました。", true);
    return 0;
  }

  const res = await send({ type: "images", urls });
  const files = (res?.files || []).map((f) => {
    const bytes = Uint8Array.from(atob(f.data), (c) => c.charCodeAt(0));
    return new File([bytes], f.name, { type: f.type });
  });
  if (!files.length) return 0;

  const dt = new DataTransfer();
  for (const f of input.multiple ? files : files.slice(0, 1)) dt.items.add(f);
  input.files = dt.files;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return dt.files.length;
}

main();
