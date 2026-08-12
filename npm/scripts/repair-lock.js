const fs = require("node:fs");
const path = require("node:path");

function lockPathFor(packageRoot) {
  return path.join(packageRoot, ".smart-search-python.lock");
}

function tryAcquire(lockPath) {
  try {
    fs.writeFileSync(lockPath, String(process.pid), { flag: "wx" });
    return true;
  } catch (error) {
    if (error.code === "EEXIST") {
      return false;
    }
    throw error;
  }
}

function release(lockPath) {
  try {
    fs.unlinkSync(lockPath);
  } catch (error) {
    // The lock may already be gone; nothing to clean up.
  }
}

function sleepSync(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function waitForRelease(lockPath, timeoutMs = 120000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (!fs.existsSync(lockPath)) {
      return true;
    }
    sleepSync(250);
  }
  return false;
}

module.exports = { lockPathFor, tryAcquire, release, waitForRelease };
