const { spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

const packageRoot = path.resolve(__dirname, "..", "..");
const venvDir = path.join(packageRoot, ".smart-search-python");
const pythonPath =
  process.platform === "win32"
    ? path.join(venvDir, "Scripts", "python.exe")
    : path.join(venvDir, "bin", "python");

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: packageRoot,
    stdio: "inherit",
    shell: options.shell || false,
    windowsHide: true
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

function capture(command, args, options = {}) {
  const result = spawnSync(command, args, {
    cwd: packageRoot,
    encoding: "utf8",
    env: options.env,
    windowsHide: true
  });
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.status !== 0 && !options.allowFailure) {
    process.stdout.write(result.stdout || "");
    process.stderr.write(result.stderr || "");
    process.exit(result.status || 1);
  }
  return options.allowFailure ? { stdout: result.stdout || "", status: result.status } : (result.stdout || "");
}

function runNpm(args) {
  if (process.env.npm_execpath) {
    run(process.execPath, [process.env.npm_execpath, ...args]);
    return;
  }
  run("npm", args, { shell: process.platform === "win32" });
}

if (!fs.existsSync(pythonPath)) {
  console.error("Missing .smart-search-python runtime. Run npm install first.");
  process.exit(1);
}

run(pythonPath, ["-m", "pip", "install", "--disable-pip-version-check", "-e", ".[dev]"]);
run(pythonPath, ["-m", "pytest"]);
run(process.execPath, ["npm/scripts/test-wrapper-repair.js"]);
run(process.execPath, ["npm/bin/smart-search.js", "--help"]);
const isolatedConfigDir = fs.mkdtempSync(path.join(os.tmpdir(), "smart-search-npm-"));
const isolatedEnv = {
  ...process.env,
  SMART_SEARCH_CONFIG_DIR: isolatedConfigDir,
  BRAVE_API_KEY: "",
  EXA_API_KEY: "",
  JINA_API_KEY: "",
  FIRECRAWL_API_KEY: "",
  TAVILY_API_KEY: "",
  TAVILY_API_URL: "",
  TAVILY_ENABLED: "false",
  TAVILY_TIMEOUT_SECONDS: ""
};
const researchJson = capture(process.execPath, [
  "npm/bin/smart-search.js",
  "research",
  "深度搜索一下最近的比特币行情"
], { env: isolatedEnv, allowFailure: true });
const researchResult = JSON.parse(researchJson.stdout);
if (researchJson.status !== 4 || researchResult.version !== 1 || researchResult.operation !== "research" ||
    researchResult.data.query !== "深度搜索一下最近的比特币行情" ||
    researchResult.status !== "failed" || researchResult.error == null ||
    researchResult.error.code !== "PROVIDER_ERROR") {
  console.error("npm wrapper must preserve v1 JSON envelopes, non-ASCII arguments, and offline failure semantics.");
  process.exit(1);
}

runNpm(["pack", "--dry-run"]);
