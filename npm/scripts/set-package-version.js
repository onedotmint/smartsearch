const fs = require("node:fs");
const path = require("node:path");

const args = process.argv.slice(2);
if (args.length !== 1) {
  console.error("Usage: node npm/scripts/set-package-version.js <stable-version>");
  process.exit(1);
}

const version = args[0];
if (!/^\d+\.\d+\.\d+$/.test(version)) {
  console.error(`Invalid stable version: ${version}`);
  process.exit(1);
}

const packageRoot = path.resolve(__dirname, "..", "..");

function fail(message) {
  console.error(message);
  process.exit(1);
}

function readJson(filePath, label) {
  let text;
  try {
    text = fs.readFileSync(filePath, "utf8");
  } catch {
    fail(`Could not read ${label}.`);
  }
  try {
    return JSON.parse(text);
  } catch {
    fail(`Could not parse ${label} as JSON.`);
  }
}

function requireVersion(data, label) {
  if (!data || typeof data.version !== "string") {
    fail(`Could not find the version field in ${label}.`);
  }
}

function requireLockRoot(data, label) {
  if (!data.packages || typeof data.packages[""] !== "object" || data.packages[""] === null) {
    fail(`Could not find the root package entry in ${label}.`);
  }
  if (typeof data.packages[""].version !== "string") {
    fail(`Could not find packages[""] version in ${label}.`);
  }
}

function findProjectVersionLine(text) {
  const lines = text.split(/\r?\n/);
  let inProject = false;
  for (let index = 0; index < lines.length; index += 1) {
    const section = lines[index].match(/^\s*\[([^\]]+)\]\s*$/);
    if (section) {
      inProject = section[1] === "project";
      continue;
    }
    if (inProject) {
      const match = lines[index].match(/^(\s*version\s*=\s*)"([^"\r\n]*)"(\s*(?:#.*)?)$/);
      if (match) {
        return { index, prefix: match[1], suffix: match[3] };
      }
    }
  }
  return null;
}

function readText(filePath, label) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    fail(`Could not read ${label}.`);
  }
}

const rootPackagePath = path.join(packageRoot, "package.json");
const rootLockPath = path.join(packageRoot, "package-lock.json");
const piPackagePath = path.join(packageRoot, "integrations", "pi", "package.json");
const piLockPath = path.join(packageRoot, "integrations", "pi", "package-lock.json");
const pyprojectPath = path.join(packageRoot, "pyproject.toml");

const rootPackage = readJson(rootPackagePath, "root package.json");
const rootLock = readJson(rootLockPath, "root package-lock.json");
const piPackage = readJson(piPackagePath, "Pi package.json");
const piLock = readJson(piLockPath, "Pi package-lock.json");
const pyproject = readText(pyprojectPath, "pyproject.toml");

requireVersion(rootPackage, "root package.json");
requireVersion(rootLock, "root package-lock.json");
requireVersion(piPackage, "Pi package.json");
requireVersion(piLock, "Pi package-lock.json");
requireLockRoot(rootLock, "root package-lock.json");
requireLockRoot(piLock, "Pi package-lock.json");
const projectVersion = findProjectVersionLine(pyproject);
if (!projectVersion) {
  console.error("Could not find the project.version field in pyproject.toml.");
  process.exit(1);
}

rootPackage.version = version;
rootLock.version = version;
rootLock.packages[""].version = version;
piPackage.version = version;
piLock.version = version;
piLock.packages[""].version = version;
const pyprojectLines = pyproject.split(/\r?\n/);
pyprojectLines[projectVersion.index] = `${projectVersion.prefix}"${version}"${projectVersion.suffix}`;

fs.writeFileSync(rootPackagePath, `${JSON.stringify(rootPackage, null, 2)}\n`);
fs.writeFileSync(rootLockPath, `${JSON.stringify(rootLock, null, 2)}\n`);
fs.writeFileSync(piPackagePath, `${JSON.stringify(piPackage, null, 2)}\n`);
fs.writeFileSync(piLockPath, `${JSON.stringify(piLock, null, 2)}\n`);
fs.writeFileSync(pyprojectPath, pyprojectLines.join("\n"));
console.log(`Set package version to ${version}.`);
