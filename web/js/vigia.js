// Vigia da tela: carregado antes de tudo. Registra no servidor qualquer erro de JavaScript
// (inclusive falha ao carregar os módulos), sem dados pessoais, para análise e correção.
(function () {
  var sent = {}, n = 0;
  function report(message, source, line, stack, where) {
    try {
      message = String(message || "").slice(0, 500); if (!message || n >= 20) return;
      var key = message + "|" + source + "|" + line; if (sent[key]) return; sent[key] = 1; n++;
      fetch("/api/saude/erro-tela", { method: "POST", credentials: "same-origin", keepalive: true,
        headers: { "Content-Type": "application/json", "X-Requested-With": "plataforma" },
        body: JSON.stringify({ message: message, source: String(source || "").replace(location.origin, "").slice(0, 200), line: String(line || 0),
          stack: String(stack || "").replace(new RegExp(location.origin, "g"), "").slice(0, 2000), where: where || window.__vigiaWhere || "", build: window.__build || "" }) }).catch(function () {});
    } catch (e) {}
  }
  window.__vigia = report;
  window.addEventListener("error", function (e) {
    if (e.target && e.target !== window && (e.target.src || e.target.href)) return report("Falha ao carregar " + (e.target.src || e.target.href), "", 0, "", "carregamento");
    report(e.message, e.filename, e.lineno, e.error && e.error.stack);
  }, true);
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason || {}; if (r && r.status && r.status < 500) return;   // respostas de negócio (400/401/403/404/409) não são erro de tela
    report((r && r.message) || String(r), "", 0, r && r.stack, "promessa");
  });
})();
