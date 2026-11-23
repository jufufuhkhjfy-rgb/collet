// Collet（http://127.0.0.1:8765）との通信はすべてここでおこなう
const API = "http://127.0.0.1:8765";

async function getQueue() {
  const res = await fetch(`${API}/api/queue`);
  if (!res.ok) throw new Error(`Collet がエラーを返しました（${res.status}）`);
  return res.json();
}

async function handle(msg) {
  switch (msg.type) {
    case "find": {
      // 投稿待ちか、さっき「ROOMで開く」で投稿済みにした候補を探す
      const res = await fetch(`${API}/api/queue/lookup?code=${encodeURIComponent(msg.code)}`);
      if (!res.ok) throw new Error(`Collet がエラーを返しました（${res.status}）`);
      return res.json();
    }
    case "imagesUsed": {
      await fetch(`${API}/api/queue/${msg.id}/images-used`, { method: "POST" });
      return { ok: true };
    }
    case "posted": {
      const res = await fetch(`${API}/api/queue/${msg.id}/posted`, { method: "POST" });
      // 404 は「もう記録済み」なので成功として扱う
      return { ok: res.ok || res.status === 404 };
    }
    case "images": {
      // ROOMのページからは Collet に直接つながらないので、ここで画像を取ってきて渡す
      const files = [];
      for (const url of msg.urls) {
        const res = await fetch(`${API}${url}`);
        if (!res.ok) continue;
        const buf = new Uint8Array(await res.arrayBuffer());
        let bin = "";
        for (let i = 0; i < buf.length; i += 0x8000) bin += String.fromCharCode(...buf.subarray(i, i + 0x8000));
        files.push({ name: url.split("/").pop(), type: res.headers.get("content-type") || "image/jpeg", data: btoa(bin) });
      }
      return { files };
    }
    case "openAll": {
      const list = await getQueue();
      for (const q of list) await chrome.tabs.create({ url: q.post_url, active: false });
      return { opened: list.length };
    }
    default:
      throw new Error(`知らないメッセージです: ${msg.type}`);
  }
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  handle(msg).then(sendResponse, (e) => sendResponse({ error: String(e?.message ?? e) }));
  return true; // 非同期で返事をする
});
