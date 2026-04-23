const { app, BrowserWindow } = require("electron");
const { spawn } = require("child_process");
const path = require("path");

let apiProc = null;

function startApi() {
  const root = path.resolve(__dirname, "..");
  const python = process.env.STOCK_ASSISTANT_PYTHON || "python";

  // Run: python -m src.api.server (uses uvicorn, serves / + /api/*)
  apiProc = spawn(python, ["-m", "src.api.server"], {
    cwd: root,
    env: {
      ...process.env,
      STOCK_ASSISTANT_HOST: process.env.STOCK_ASSISTANT_HOST || "127.0.0.1",
      STOCK_ASSISTANT_PORT: process.env.STOCK_ASSISTANT_PORT || "8787",
    },
    stdio: "pipe",
  });

  apiProc.stdout.on("data", (d) => console.log(String(d)));
  apiProc.stderr.on("data", (d) => console.error(String(d)));
  apiProc.on("exit", (code) => console.log("API exited:", code));
}

function createWindow() {
  const win = new BrowserWindow({
    width: 1100,
    height: 780,
    backgroundColor: "#0f172a",
    webPreferences: {
      contextIsolation: true
    }
  });

  const port = process.env.STOCK_ASSISTANT_PORT || "8787";
  win.loadURL(`http://127.0.0.1:${port}/`);
}

app.whenReady().then(() => {
  startApi();
  createWindow();
});

app.on("window-all-closed", () => {
  if (apiProc) {
    try {
      apiProc.kill();
    } catch {}
    apiProc = null;
  }
  if (process.platform !== "darwin") app.quit();
});

