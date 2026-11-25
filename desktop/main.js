// T24 (Q4 owner 2026-11-19, PRD §12): wrapper Electron Win-first.
// Membungkus UI localhost yang ada; sidecar Python (PyInstaller) + Camoufox terbundel.
// Dev (tidak packaged): sidecar = `python -m app` dari repo induk.
const { app, BrowserWindow, dialog, shell } = require("electron");
const path = require("path");
const net = require("net");
const { spawn } = require("child_process");
const http = require("http");

const PORT = Number(process.env.SLA_PORT || 8765);
const BASE = `http://127.0.0.1:${PORT}`;
let sidecar = null;
let win = null;
let quitting = false;

function portFree() {
  return new Promise((resolve) => {
    const s = net.createServer();
    s.once("error", () => resolve(false));
    s.once("listening", () => s.close(() => resolve(true)));
    s.listen(PORT, "127.0.0.1");
  });
}

function startSidecar() {
  const env = {
    ...process.env,
    SLA_DATA_DIR: path.join(app.getPath("userData"), "data"),
    SLA_PORT: String(PORT),
    SLA_NO_BROWSER: "1", // jendela Electron yang menampilkan UI, bukan browser OS
  };
  console.log("[sla] packaged:", app.isPackaged, "port:", PORT);
  if (app.isPackaged) {
    // Camoufox terbundel di resources/camoufox (override resolusi path; lihat app/browser.py)
    env.SLA_CAMOUFOX_DIR = path.join(process.resourcesPath, "camoufox");
    const exe = path.join(process.resourcesPath, "sidecar",
      process.platform === "win32" ? "sla-sidecar.exe" : "sla-sidecar");
    console.log("[sla] spawn sidecar:", exe);
    sidecar = spawn(exe, [], { env, stdio: "ignore", windowsHide: true });
    sidecar.on("error", (e) => console.error("[sla] spawn error:", e.message));
  } else {
    env.SLA_HEADLESS = "1"; // dev Electron pun headless; headed hanya `python -m app`
    sidecar = spawn(process.env.SLA_PYTHON || "python", ["-m", "app"], {
      cwd: path.join(__dirname, ".."),
      env,
      stdio: "ignore",
      windowsHide: true,
    });
  }
  console.log("[sla] sidecar pid:", sidecar && sidecar.pid);
  sidecar.on("exit", (code) => {
    console.log("[sla] sidecar exit code:", code);
    sidecar = null;
    if (!quitting && win && code !== 0) {
      dialog.showErrorBox(
        "Sidecar berhenti",
        `Proses scraping lokal berhenti (kode ${code}). Buka ulang aplikasi; job yang sedang berjalan ditandai failed [interrupted] dan bisa di-retry.`
      );
      app.quit();
    }
  });
}

function waitHealthy(timeoutMs = 90000) {
  const deadline = Date.now() + timeoutMs;
  return new Promise((resolve) => {
    const tick = () => {
      const req = http.get(BASE + "/healthz", (res) => {
        res.resume();
        console.log("[sla] healthz:", res.statusCode);
        resolve(res.statusCode === 200);
      });
      req.on("error", () => {
        if (Date.now() > deadline) resolve(false);
        else setTimeout(tick, 500);
      });
      req.setTimeout(2000, () => req.destroy());
    };
    tick();
  });
}

async function createWindow() {
  win = new BrowserWindow({
    width: 1440,
    height: 900,
    title: "Scraping Lokal Otomatis",
    webPreferences: { contextIsolation: true, nodeIntegration: false },
  });
  win.on("closed", () => { win = null; });
  await win.loadURL(BASE);
  // Tanpa handler ini, download Excel di app packaged berakhir `cancelled` (save dialog
  // tak resolve) — terbukti repro. Simpan langsung ke folder Downloads OS + buka lokasinya.
  win.webContents.session.on("will-download", (event, item) => {
    item.setSavePath(path.join(app.getPath("downloads"), item.getFilename()));
    item.once("done", (e, state) => {
      if (state === "completed") shell.showItemInFolder(item.getSavePath());
    });
  });
}

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(async () => {
    if (!(await portFree())) {
      dialog.showErrorBox(
        "Port dipakai",
        `Port ${PORT} sedang dipakai aplikasi lain di komputer ini. Tutup aplikasi itu (atau set env SLA_PORT ke port bebas) lalu buka ulang.`
      );
      app.quit();
      return;
    }
    startSidecar();
    const ok = await waitHealthy();
    console.log("[sla] healthy:", ok);
    if (!ok) {
      dialog.showErrorBox(
        "Gagal memulai",
        `UI lokal tidak menjawab di ${BASE} dalam 90 detik. Tutup aplikasi lalu buka ulang; bila berulang, lapor developer.`
      );
      app.quit();
      return;
    }
    await createWindow();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", () => {
    quitting = true;
    if (sidecar) sidecar.kill(); // E5: job aktif -> failed [interrupted] saat startup berikutnya
  });
}
