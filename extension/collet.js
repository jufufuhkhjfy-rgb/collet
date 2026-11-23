// Collet の画面に「拡張機能が入っている」ことを知らせ、「まとめて開く」を中継する
document.documentElement.dataset.colletExt = "1";

window.addEventListener("message", (e) => {
  if (e.source !== window || e.data?.type !== "collet-open-all") return;
  chrome.runtime.sendMessage({ type: "openAll" }, (res) => {
    window.postMessage({ type: "collet-open-all-result", ...(res ?? { error: "拡張機能から返事がありませんでした" }) }, "*");
  });
});
