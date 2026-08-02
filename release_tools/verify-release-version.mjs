import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');

const version = readPythonVersion();
const expectedTag = `app-v${version}`;
const actualTag = process.argv[2] || process.env.GITHUB_REF_NAME || '';

if (actualTag !== expectedTag) {
  console.error(`Release tag mismatch: expected ${expectedTag}, got ${actualTag || '<empty>'}.`);
  process.exit(1);
}

verifyChangelog(version);

console.log(`Release version verified: ${version}`);

function readPythonVersion() {
  const versionFile = readFileSync(path.join(repoRoot, 'software', 'study_runner', 'version.py'), 'utf8');
  const versionMatch = versionFile.match(/^__version__\s*=\s*"(?<version>[^"]+)"/m);
  const version = versionMatch?.groups?.version;
  if (!version) {
    throw new Error('Could not find __version__ in software/study_runner/version.py.');
  }
  return version;
}

function verifyChangelog(version) {
  const changelog = readFileSync(path.join(repoRoot, 'CHANGELOG.md'), 'utf8');
  const escapedVersion = version.replaceAll('.', '\\.');
  const matches = changelog.match(
    new RegExp(`^## ${escapedVersion}(?:[ \\t]+-[ \\t]+[^\\n]+)?[ \\t]*$`, 'gm'),
  ) || [];
  if (matches.length !== 1) {
    throw new Error(`CHANGELOG.md must contain exactly one release section for ${version}.`);
  }
}
