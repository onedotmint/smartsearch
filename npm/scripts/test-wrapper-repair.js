const assert = require("node:assert");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const vm = require("node:vm");

const repairLock = require("./repair-lock");

const wrapperPath = path.resolve(__dirname, "..", "bin", "smart-search.js");
const wrapperSource = fs.readFileSync(wrapperPath, "utf8");

function runWrapper({ runtimeExists, repairStatus = 0, repairCreatesRuntime = true }) {
  let repairedRuntimeExists = runtimeExists;
  const spawnSyncCalls = [];
  const spawnCalls = [];
  const exits = [];
  const stderr = [];
  const signalHandlers = {};
  const killedSignals = [];

  const fakeFs = {
    existsSync(filePath) {
      const normalized = filePath.replaceAll("\\", "/");
      if (
        normalized.endsWith("/.smart-search-python/Scripts/python.exe") ||
        normalized.endsWith("/.smart-search-python/bin/python")
      ) {
        return repairedRuntimeExists;
      }
      return fs.existsSync(filePath);
    }
  };

  const fakeProcess = {
    ...process,
    argv: ["node", wrapperPath, "--version"],
    env: {},
    cwd: () => "C:\\caller",
    exit(code) {
      exits.push(code);
      throw new Error(`process.exit(${code})`);
    },
    kill() {},
    on(event, handler) {
      signalHandlers[event] = handler;
      return this;
    }
  };

  const fakeChildProcess = {
    spawnSync(command, args, options) {
      spawnSyncCalls.push({ command, args, options });
      if (repairStatus instanceof Error) {
        return { error: repairStatus };
      }
      if (repairCreatesRuntime) {
        repairedRuntimeExists = true;
      }
      return { status: repairStatus };
    },
    spawn(command, args, options) {
      spawnCalls.push({ command, args, options });
      return {
        on() {
          return this;
        },
        kill(signal) {
          killedSignals.push(signal);
        }
      };
    }
  };

  const context = {
    __dirname: path.dirname(wrapperPath),
    console: { error(message = "") { stderr.push(String(message)); }, log() {} },
    process: fakeProcess,
    require(moduleName) {
      if (moduleName === "node:child_process") {
        return fakeChildProcess;
      }
      if (moduleName === "node:fs") {
        return fakeFs;
      }
      if (moduleName === "node:path") {
        return path;
      }
      return require(moduleName);
    }
  };

  try {
    vm.runInNewContext(wrapperSource, context, { filename: wrapperPath });
  } catch (error) {
    if (!String(error.message).startsWith("process.exit(")) {
      throw error;
    }
  }

  return { spawnSyncCalls, spawnCalls, exits, stderr, signalHandlers, killedSignals };
}

const healthy = runWrapper({ runtimeExists: true });
assert.strictEqual(healthy.spawnSyncCalls.length, 0);
assert.strictEqual(healthy.spawnCalls.length, 1);

const repaired = runWrapper({ runtimeExists: false });
assert.strictEqual(repaired.spawnSyncCalls.length, 1);
assert(
  repaired.spawnSyncCalls[0].args[0].replaceAll("\\", "/").endsWith("/npm/scripts/postinstall.js"),
  "missing runtime should invoke package postinstall repair"
);
assert.strictEqual(repaired.spawnCalls.length, 1);
assert.deepStrictEqual(Array.from(repaired.spawnCalls[0].args.slice(0, 2)), ["-m", "smart_search.cli"]);
assert.strictEqual(repaired.exits.length, 0);

const failedRepair = runWrapper({
  runtimeExists: false,
  repairStatus: 1,
  repairCreatesRuntime: false
});
assert.strictEqual(failedRepair.spawnSyncCalls.length, 1);
assert.strictEqual(failedRepair.spawnCalls.length, 0);
assert.deepStrictEqual(failedRepair.exits, [1]);
assert(
  failedRepair.stderr.includes("  npm install -g @onedotmint/smart-search"),
  "failed repair should recommend reinstalling the stable package"
);
assert(
  !failedRepair.stderr.some((message) => message.includes("@next")),
  "failed repair should not recommend the next release tag"
);

const repairSpawnError = runWrapper({
  runtimeExists: false,
  repairStatus: new Error("postinstall unavailable"),
  repairCreatesRuntime: false
});
assert.strictEqual(repairSpawnError.spawnSyncCalls.length, 1);
assert.strictEqual(repairSpawnError.spawnCalls.length, 0);
assert.deepStrictEqual(repairSpawnError.exits, [5]);
assert(
  repairSpawnError.stderr.includes("  npm install -g @onedotmint/smart-search"),
  "repair spawn errors should recommend reinstalling the stable package"
);
assert(
  !repairSpawnError.stderr.some((message) => message.includes("@next")),
  "repair spawn errors should not recommend the next release tag"
);

// Signal forwarding: the wrapper registers SIGINT/SIGTERM handlers that
// forward to the Python child, guarding against duplicate forwarding.
const forwarded = runWrapper({ runtimeExists: true });
assert.strictEqual(typeof forwarded.signalHandlers.SIGINT, "function");
assert.strictEqual(typeof forwarded.signalHandlers.SIGTERM, "function");
forwarded.signalHandlers.SIGINT();
forwarded.signalHandlers.SIGINT(); // duplicate is ignored while the child exits
assert.deepStrictEqual(forwarded.killedSignals, ["SIGINT"]);
forwarded.signalHandlers.SIGTERM(); // still ignored after the first signal
assert.deepStrictEqual(forwarded.killedSignals, ["SIGINT"]);

// Repair lock: a second process observes a held lock, a released lock can be
// re-acquired, and waitForRelease returns once the lock is gone.
const tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), "smart-search-lock-"));
try {
  const lockPath = path.join(tmpDir, "lock");
  assert.strictEqual(repairLock.tryAcquire(lockPath), true);
  assert.ok(fs.existsSync(lockPath), "lock file must exist after acquire");
  // A concurrent second process cannot acquire while the lock is held.
  assert.strictEqual(repairLock.tryAcquire(lockPath), false);
  // waitForRelease times out while the lock stays held.
  assert.strictEqual(repairLock.waitForRelease(lockPath, 100), false);
  repairLock.release(lockPath);
  assert.ok(!fs.existsSync(lockPath), "lock file must be removed after release");
  // waitForRelease returns immediately once the lock is gone.
  assert.strictEqual(repairLock.waitForRelease(lockPath, 1000), true);
  assert.strictEqual(repairLock.tryAcquire(lockPath), true);
  repairLock.release(lockPath);
} finally {
  fs.rmSync(tmpDir, { recursive: true, force: true });
}
