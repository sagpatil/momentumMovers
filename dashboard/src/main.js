import "./styles.css";
import { renderApp } from "./render.js";

// latest.json lives in public/data/ and is copied to the build root by Vite.
const DATA_URL = `${import.meta.env.BASE_URL}data/latest.json`;

async function boot() {
  const app = document.getElementById("app");
  try {
    const res = await fetch(DATA_URL, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const snapshot = await res.json();
    renderApp(app, snapshot);
  } catch (err) {
    app.innerHTML = `<div class="wrap"><div class="empty">Couldn't load snapshot data.<br><small>${err.message}</small></div></div>`;
  }
}

boot();
