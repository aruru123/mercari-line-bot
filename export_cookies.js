/**
 * メルカリのクッキーをエクスポートするスクリプト
 *
 * 使い方：
 * 1. Chrome で https://jp.mercari.com にログインする
 * 2. F12 → Console タブを開く
 * 3. このスクリプト全体を貼り付けて Enter
 * 4. 「クッキーをコピーしました！」と表示されたらクリップボードに JSON がある
 * 5. Railway の環境変数 MERCARI_COOKIES にそのまま貼り付ける
 */

(function () {
  const domain = "jp.mercari.com";
  const cookies = document.cookie
    .split(";")
    .map((c) => c.trim())
    .filter(Boolean)
    .map((c) => {
      const eqIdx = c.indexOf("=");
      return {
        name: c.substring(0, eqIdx).trim(),
        value: c.substring(eqIdx + 1).trim(),
        domain: domain,
        path: "/",
        secure: true,
        sameSite: "Lax",
      };
    });

  const json = JSON.stringify(cookies);
  navigator.clipboard
    .writeText(json)
    .then(() => {
      console.log("✅ クッキーをコピーしました！");
      console.log("Railway の MERCARI_COOKIES 環境変数に貼り付けてください。");
      console.log("クッキー数:", cookies.length);
    })
    .catch(() => {
      console.log("クリップボードへの書き込みに失敗しました。以下をコピーしてください：");
      console.log(json);
    });
})();
