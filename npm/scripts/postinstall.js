const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const { lockPathFor, tryAcquire, release, waitForRelease } = require("./repair-lock");

const packageRoot = path.resolve(__dirname, "..", "..");
const venvDir = path.join(packageRoot, ".smart-search-python");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: packageRoot,
    stdio: options.stdio || "inherit",
    encoding: "utf8",
    windowsHide: true
  });

  if (result.error) {
    return { ok: false, error: result.error };
  }
  return { ok: result.status === 0, status: result.status, stdout: result.stdout || "" };
}

function pythonCandidates() {
  if (process.platform === "win32") {
    return [
      { command: "py", args: ["-3"] },
      { command: "python", args: [] },
      { command: "python3", args: [] }
    ];
  }
  return [
    { command: "python3", args: [] },
    { command: "python", args: [] }
  ];
}

function findPython() {
  const probe = [
    "-c",
    "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)"
  ];

  for (const candidate of pythonCandidates()) {
    const result = run(candidate.command, [...candidate.args, ...probe], { stdio: "pipe" });
    if (result.ok) {
      return candidate;
    }
  }
  return null;
}

function venvPython() {
  return process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");
}

const python = findPython();
if (!python) {
  console.error("smart-search requires Python 3.10 or newer.");
  console.error("Install Python, then run: npm install -g @onedotmint/smart-search@latest");
  process.exit(1);
}

// Serialize runtime repair so two concurrent first runs cannot both create the
// shared venv. A second process observes the lock, waits for the owner to
// finish, then re-checks whether the venv now exists (idempotent repair).
function repair() {
  const lockPath = lockPathFor(packageRoot);

  if (!tryAcquire(lockPath)) {
    console.log("A concurrent smart-search runtime repair is in progress; waiting for it to finish...");
    if (!waitForRelease(lockPath)) {
      console.error("Timed out waiting for a concurrent smart-search runtime repair.");
      return 1;
    }
    if (!tryAcquire(lockPath)) {
      console.error("Could not acquire the smart-search runtime repair lock.");
      return 1;
    }
  }

  try {
    if (!fs.existsSync(venvPython())) {
      console.log("Creating smart-search Python runtime...");
      const created = run(python.command, [...python.args, "-m", "venv", venvDir]);
      if (!created.ok) {
        console.error("Failed to create the smart-search Python virtual environment.");
        return created.status || 1;
      }
    }

    const py = venvPython();

    console.log("Installing smart-search Python package...");
    const install = run(py, [
      "-m",
      "pip",
      "install",
      "--disable-pip-version-check",
      packageRoot
    ]);

    if (!install.ok) {
      console.error("Failed to install the bundled smart-search Python package.");
      return install.status || 1;
    }
    return 0;
  } finally {
    release(lockPath);
  }
}

process.exit(repair());
