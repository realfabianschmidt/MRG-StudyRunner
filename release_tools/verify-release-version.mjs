import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const desktopRoot = path.join(repoRoot, 'desktop');

function readJson(relativePath) {
  return JSON.parse(readFileSync(path.join(desktopRoot, relativePath), 'utf8'));
}

function readCargoVersion() {
  const cargoToml = readFileSync(path.join(desktopRoot, 'src-tauri', 'Cargo.toml'), 'utf8');
  const packageSection = cargoToml.match(/^\[package\]$(?<body>[\s\S]*?)(?=^\[|$)/m);
  const body = packageSection?.groups?.body || cargoToml;
  const version = body.match(/^version\s*=\s*"(?<version>[^"]+)"/m)?.groups?.version;
  if (!version) {
    throw new Error('Could not find [package] version in src-tauri/Cargo.toml.');
  }
  return version;
}

const packageVersion = readJson('package.json').version;
const tauriVersion = readJson(path.join('src-tauri', 'tauri.conf.json')).version;
const cargoVersion = readCargoVersion();
const versions = new Set([packageVersion, tauriVersion, cargoVersion]);

if (versions.size !== 1) {
  console.error('Release version mismatch:');
  console.error(`- package.json: ${packageVersion}`);
  console.error(`- src-tauri/tauri.conf.json: ${tauriVersion}`);
  console.error(`- src-tauri/Cargo.toml: ${cargoVersion}`);
  process.exit(1);
}

const version = packageVersion;
const expectedTag = `app-v${version}`;
const actualTag = process.argv[2] || process.env.GITHUB_REF_NAME || '';

if (actualTag !== expectedTag) {
  console.error(`Release tag mismatch: expected ${expectedTag}, got ${actualTag || '<empty>'}.`);
  process.exit(1);
}

console.log(`Release version verified: ${version}`);
