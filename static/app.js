/* Scraping Lokal Otomatis — interaksi shell (htmx + lucide + toast + modal). */
(function () {
  "use strict";

  function icons() { if (window.lucide) lucide.createIcons(); }
  document.addEventListener("DOMContentLoaded", icons);
  document.body.addEventListener("htmx:afterSwap", function (e) {
    icons();
    // Setelah opsi akun dimuat ulang, picu change agar kandidat cabang ikut dimuat
    if (e.detail.target && e.detail.target.id === "account-select") {
      var sel = e.detail.target;
      if (sel.value) sel.dispatchEvent(new Event("change"));
    }
    if (e.detail.target && e.detail.target.id === "modal-root") {
      if (window.htmx) htmx.process(e.detail.target);  // pastikan form modal ter-bind hx-post
      var first = document.querySelector("#modal-root input:not([type=hidden]), #modal-root select");
      if (first) first.focus();
    }
  });

  // ---------- Sidebar mobile ----------
  window.toggleSidebar = function () {
    document.getElementById("sidebar").classList.toggle("-translate-x-full");
    document.getElementById("sidebar-backdrop").classList.toggle("hidden");
  };

  // ---------- Modal ----------
  // tunda satu tick: event HX-Trigger lain (refreshAccounts) harus bubble dulu sebelum form dilepas
  window.closeModal = function () {
    setTimeout(function () { document.getElementById("modal-root").innerHTML = ""; }, 0);
  };
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") { closeModal(); }
  });
  document.body.addEventListener("closeModal", closeModal);

  // ---------- Toast ----------
  var TONE = {
    success: { border: "border-l-success", icon: "circle-check", text: "text-success" },
    error: { border: "border-l-danger", icon: "circle-alert", text: "text-danger" },
    info: { border: "border-l-primary", icon: "info", text: "text-primary" },
    warning: { border: "border-l-warning", icon: "triangle-alert", text: "text-warning" }
  };
  window.showToast = function (msg, kind) {
    var t = TONE[kind] || TONE.info;
    var root = document.getElementById("toast-root");
    var el = document.createElement("div");
    el.className = "card border-l-4 " + t.border + " px-4 py-3 flex items-start gap-3 shadow-modal " +
      "opacity-0 translate-y-2 transition-all duration-200";
    el.setAttribute("role", "status");
    var ic = document.createElement("i");
    ic.setAttribute("data-lucide", t.icon);
    ic.className = "w-4 h-4 mt-0.5 shrink-0 " + t.text;
    var span = document.createElement("span");
    span.className = "text-[13px] text-ink leading-snug flex-1";
    span.textContent = msg;
    var x = document.createElement("button");
    x.className = "text-subtle hover:text-ink cursor-pointer shrink-0";
    x.setAttribute("aria-label", "Tutup notifikasi");
    x.innerHTML = '<i data-lucide="x" class="w-3.5 h-3.5"></i>';
    x.onclick = function () { el.remove(); };
    el.appendChild(ic); el.appendChild(span); el.appendChild(x);
    root.appendChild(el);
    icons();
    requestAnimationFrame(function () { el.classList.remove("opacity-0", "translate-y-2"); });
    setTimeout(function () {
      el.classList.add("opacity-0", "translate-y-2");
      setTimeout(function () { el.remove(); }, 220);
    }, 4500);
  };
  document.body.addEventListener("toast", function (e) {
    var d = e.detail || {};
    showToast(d.msg || "", d.kind || "info");
  });

  // ---------- Salin detail error (redacted, S2) ----------
  document.addEventListener("click", function (e) {
    var btn = e.target.closest("[data-copy]");
    if (!btn) return;
    fetch(btn.getAttribute("data-copy"), { credentials: "same-origin" })
      .then(function (r) { if (!r.ok) throw new Error(r.status); return r.text(); })
      .then(function (text) { return navigator.clipboard.writeText(text); })
      .then(function () { showToast("Detail error disalin (redacted) — siap dikirim ke developer", "success"); })
      .catch(function () { showToast("Gagal menyalin detail error", "error"); });
  });

  // ---------- Sinkronisasi filter/sort/halaman dengan polling 2 detik ----------
  function setVal(id, v) { var el = document.getElementById(id); if (el) el.value = v == null ? "" : v; }
  document.body.addEventListener("htmx:configRequest", function (e) {
    var path = e.detail.path || "";
    if (path.indexOf("/partials/jobs") !== 0 || path.indexOf("/partials/jobs/") === 0) return;
    var p = e.detail.parameters;
    var elt = e.detail.elt;
    var fromToolbar = elt && ["q", "f-status", "f-pos", "f-from", "f-to"].indexOf(elt.id) >= 0;
    if (fromToolbar) p.page = "1";
    setVal("f-page", p.page || "1");
    setVal("f-sort", p.sort || "id");
    setVal("f-dir", p.dir || "desc");
    if (elt && elt.classList && elt.classList.contains("stat-card")) {
      setVal("f-status", p.status || "");
    }
    var n = ["q", "status", "pos", "date_from", "date_to"].filter(function (k) { return p[k]; }).length;
    var badge = document.getElementById("filter-count");
    if (badge) { badge.textContent = n; badge.classList.toggle("hidden", n === 0); }
  });

  window.resetJobFilters = function () {
    ["q", "f-status", "f-pos", "f-from", "f-to"].forEach(function (id) { setVal(id, ""); });
    setVal("f-page", "1"); setVal("f-sort", "id"); setVal("f-dir", "desc");
    if (window.htmx) {
      htmx.ajax("GET", "/partials/jobs", {
        target: "#jobs-table", swap: "outerHTML",
        values: { page: "1", sort: "id", dir: "desc" }
      });
    }
  };

  // ---------- Tambah cabang manual (E14) ----------
  window.addManualBranch = function () {
    var kEl = document.getElementById("manual-key");
    var nEl = document.getElementById("manual-name");
    var k = kEl.value.trim(), n = nEl.value.trim();
    if (!k) { showToast("Isi key cabang dulu (mis. outlet-01)", "error"); kEl.focus(); return; }
    var label = document.createElement("label");
    label.className = "flex items-center gap-3 px-3 py-2.5 rounded-control border border-hairline cursor-pointer " +
      "transition-colors has-[:checked]:bg-primary-subtle has-[:checked]:border-primary/30 hover:bg-surface-hover";
    var cb = document.createElement("input");
    cb.type = "checkbox"; cb.name = "branches"; cb.value = k + "|" + (n || k); cb.checked = true;
    cb.className = "w-4 h-4 rounded accent-primary cursor-pointer";
    var key = document.createElement("span");
    key.className = "font-mono text-[12px] text-muted"; key.textContent = k;
    var name = document.createElement("span");
    name.className = "text-[13px] font-medium text-ink"; name.textContent = n || k;
    label.appendChild(cb); label.appendChild(key); label.appendChild(name);
    document.getElementById("manual-branches").appendChild(label);
    kEl.value = ""; nEl.value = ""; kEl.focus();
  };
})();