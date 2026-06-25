import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const desktopRoot = path.join(repoRoot, 'desktop');
const tauriRoot = path.join(desktopRoot, 'src-tauri');
const isWindows = process.platform === 'win32';
const npmCommand = isWindows ? 'npm.cmd' : 'npm';

const command = process.argv[2];
const versionInput = process.argv[3];
const flags = new Set(process.argv.slice(4));

function printHelp() {
  console.log(`
Study Runner release helper

Usage:
  node release_tools/release-study-runner.mjs release <patch|minor|major|version> [--dry-run] [--full-checks] [--skip-checks]
  node release_tools/release-study-runner.mjs prepare <patch|minor|major|version> [--full-checks] [--skip-checks]
  node release_tools/release-study-runner.mjs publish <version>
  node release_tools/release-study-runner.mjs status

Recommended non-coder command on Windows:
  .\\release.ps1 patch

Requirements on the machine that runs a release:
  Git, Node.js, and Python 3.12. Full local checks also need npm and Rust (cargo).
  GitHub CLI is not required. GitHub Actions builds the installer artifacts after the tag is pushed.

The release command bumps versions on main, runs fast local checks, commits,
pushes main, then pushes app-v<version> to start the release workflow.
`);
}

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

function run(cmd, args, options = {}) {
  const capture = Boolean(options.capture);
  const result = spawnSync(cmd, args, {
    cwd: options.cwd || repoRoot,
    stdio: capture ? 'pipe' : 'inherit',
    encoding: capture ? 'utf8' : undefined,
    shell: false,
    maxBuffer: 100 * 1024 * 1024,
  });

  if (result.error) {
    if (options.allowFailure) {
      return result;
    }
    fail(`Could not start '${cmd}'. ${result.error.message}`);
  }

  if (result.status !== 0) {
    if (capture && result.stderr) {
      process.stderr.write(result.stderr);
    }
    if (options.allowFailure) {
      return result;
    }
    fail(`Command failed: ${cmd} ${args.join(' ')}`);
  }

  if (capture && options.allowFailure) {
    return result;
  }
  return capture ? result.stdout.trim() : result;
}

function git(args, options = {}) {
  return run('git', args, options);
}

function readText(relativePath) {
  return readFileSync(path.join(repoRoot, relativePath), 'utf8');
}

function writeText(relativePath, value) {
  writeFileSync(path.join(repoRoot, relativePath), value, 'utf8');
}

function readJson(relativePath) {
  return JSON.parse(readText(relativePath));
}

function writeJson(relativePath, value) {
  writeText(relativePath, `${JSON.stringify(value, null, 2)}\n`);
}

function currentDesktopVersion() {
  return readJson('desktop/package.json').version;
}

function ensureSemver(value) {
  if (!/^\d+\.\d+\.\d+$/.test(value || '')) {
    fail(`Expected a SemVer version like 0.3.0, got: ${value || '<empty>'}`);
  }
}

function resolveNextVersion(input) {
  const current = currentDesktopVersion();
  const [major, minor, patch] = current.split('.').map((part) => Number.parseInt(part, 10));
  if (input === 'patch') return `${major}.${minor}.${patch + 1}`;
  if (input === 'minor') return `${major}.${minor + 1}.0`;
  if (input === 'major') return `${major + 1}.0.0`;
  ensureSemver(input);
  return input;
}

function releaseTagName(version) {
  return `app-v${version}`;
}

function currentBranch() {
  return git(['branch', '--show-current'], { capture: true });
}

function headSha(ref = 'HEAD') {
  return git(['rev-parse', ref], { capture: true });
}

function ensureTagDoesNotExist(tagName) {
  const local = git(['rev-parse', '--verify', tagName], {
    capture: true,
    allowFailure: true,
  });
  if (local.status === 0) {
    fail(`Local tag already exists: ${tagName}`);
  }

  if (git(['ls-remote', '--tags', 'origin', tagName], { capture: true })) {
    fail(`Remote tag already exists: ${tagName}`);
  }
}

function ensureCleanWorkingTree() {
  const status = git(['status', '--porcelain'], { capture: true });
  if (status) {
    fail(`Working tree must be clean before releasing:\n${status}`);
  }
}

function ensureDirectReleaseStartingPoint() {
  git(['fetch', 'origin', 'main', '--tags']);
  const branch = currentBranch();

  if (branch !== 'main') {
    fail(`You are on '${branch}'. Switch to main before starting a release.`);
  }

  ensureCleanWorkingTree();

  if (headSha('HEAD') !== headSha('origin/main')) {
    fail('Local main is not equal to origin/main. Pull or push main before releasing.');
  }
}

function replacePackageVersion(relativePath, nextVersion) {
  const pkg = readJson(relativePath);
  pkg.version = nextVersion;
  writeJson(relativePath, pkg);
}

function replacePackageLockVersion(nextVersion) {
  const lock = readJson('desktop/package-lock.json');
  lock.version = nextVersion;
  if (lock.packages?.['']) {
    lock.packages[''].version = nextVersion;
  }
  writeJson('desktop/package-lock.json', lock);
}

function replaceTauriVersion(nextVersion) {
  const config = readJson('desktop/src-tauri/tauri.conf.json');
  config.version = nextVersion;
  writeJson('desktop/src-tauri/tauri.conf.json', config);
}

function replaceCargoTomlVersion(nextVersion) {
  const relativePath = 'desktop/src-tauri/Cargo.toml';
  const input = readText(relativePath);
  const output = input.replace(
    /(^\[package\][\s\S]*?^version\s*=\s*")[^"]+(")/m,
    `$1${nextVersion}$2`,
  );
  if (output === input) {
    fail(`Could not update ${relativePath}.`);
  }
  writeText(relativePath, output);
}

function replaceCargoLockVersion(nextVersion) {
  const relativePath = 'desktop/src-tauri/Cargo.lock';
  const input = readText(relativePath);
  const output = input.replace(
    /(\[\[package\]\]\r?\nname = "study-runner-desktop"\r?\nversion = ")[^"]+(")/,
    `$1${nextVersion}$2`,
  );
  if (output === input) {
    fail(`Could not update ${relativePath}.`);
  }
  writeText(relativePath, output);
}

function bumpVersions(nextVersion) {
  replacePackageVersion('desktop/package.json', nextVersion);
  replacePackageLockVersion(nextVersion);
  replaceTauriVersion(nextVersion);
  replaceCargoTomlVersion(nextVersion);
  replaceCargoLockVersion(nextVersion);
}

function ensureToolchain({ full = false } = {}) {
  const tools = [
    { cmd: 'node', args: ['--version'], hint: 'Install Node.js from https://nodejs.org' },
    { cmd: 'python', args: ['--version'], hint: 'Install Python 3.12 from https://python.org' },
    { cmd: 'git', args: ['--version'], hint: 'Install Git from https://git-scm.com' },
  ];
  if (full) {
    tools.push(
      { cmd: npmCommand, args: ['--version'], hint: 'npm ships with Node.js' },
      { cmd: 'cargo', args: ['--version'], hint: 'Install Rust from https://rustup.rs' },
    );
  }

  const missing = [];
  for (const tool of tools) {
    const result = run(tool.cmd, tool.args, { capture: true, allowFailure: true });
    if (result.error || result.status !== 0) {
      missing.push(`- ${tool.cmd}: ${tool.hint}`);
    }
  }
  if (missing.length > 0) {
    fail(`Some tools needed for release checks are missing:\n${missing.join('\n')}\n\nInstall them, or run with --skip-checks to skip local checks.`);
  }
}

function runChecks(nextVersion) {
  const fullChecks = flags.has('--full-checks');
  ensureToolchain({ full: fullChecks });
  run('node', ['--check', 'desktop/web/main.js']);
  run('node', ['--check', 'release_tools/verify-release-version.mjs']);
  run('node', ['--check', 'release_tools/release-study-runner.mjs']);
  run('node', ['release_tools/verify-release-version.mjs', releaseTagName(nextVersion)]);
  run('python', ['-m', 'unittest', 'discover', path.join('software', 'tests')]);

  if (fullChecks) {
    run(npmCommand, ['--prefix', 'desktop', 'run', 'build:sidecar']);
    run('cargo', ['check', '-q'], { cwd: tauriRoot });
  }

  git(['diff', '--check']);
}

function stagedFiles() {
  const output = git(['diff', '--cached', '--name-only', '--diff-filter=ACMR'], { capture: true });
  return output ? output.split(/\r?\n/).filter(Boolean) : [];
}

function ensureNoStagedSecrets(files) {
  const blockedExtensions = /\.(pfx|p12|pem|p8|key)$/i;
  const blockedPath = /(^|\/|\\)\.secrets($|\/|\\)/i;
  const privateKeyNeedle = ['BEGIN ', 'PRIVATE KEY'].join('');
  const secretAssignment = /\b(TAURI_SIGNING_PRIVATE_KEY|WINDOWS_CERTIFICATE|APPLE_CERTIFICATE)\s*=/;

  for (const file of files) {
    if (blockedExtensions.test(file) || blockedPath.test(file)) {
      fail(`Refusing to commit possible secret file: ${file}`);
    }

    const content = git(['show', `:${file}`], {
      capture: true,
      allowFailure: true,
    });
    if (content.status !== 0) {
      continue;
    }

    if (content.stdout.includes(privateKeyNeedle) || secretAssignment.test(content.stdout)) {
      fail(`Refusing to commit possible secret content in: ${file}`);
    }
  }
}

function commitVersionChanges(version, message) {
  git(['add', '-A']);
  const files = stagedFiles();
  if (files.length === 0) {
    fail('No changes are staged. Make changes first, or choose a new version.');
  }

  ensureNoStagedSecrets(files);
  git(['commit', '-m', message || `Release Study Runner ${version}`]);
}

function versionFromCargoToml(content) {
  const packageSection = content.match(/^\[package\]$(?<body>[\s\S]*?)(?=^\[|$)/m);
  const body = packageSection?.groups?.body || content;
  return body.match(/^version\s*=\s*"(?<version>[^"]+)"/m)?.groups?.version;
}

function readVersionFromGit(ref, relativePath, reader) {
  const content = git(['show', `${ref}:${relativePath}`], { capture: true });
  return reader(content);
}

function verifyRemoteMainVersion(version) {
  const versions = {
    'desktop/package.json': readVersionFromGit('origin/main', 'desktop/package.json', (content) => JSON.parse(content).version),
    'desktop/src-tauri/tauri.conf.json': readVersionFromGit('origin/main', 'desktop/src-tauri/tauri.conf.json', (content) => JSON.parse(content).version),
    'desktop/src-tauri/Cargo.toml': readVersionFromGit('origin/main', 'desktop/src-tauri/Cargo.toml', versionFromCargoToml),
  };

  const mismatches = Object.entries(versions).filter(([, value]) => value !== version);
  if (mismatches.length > 0) {
    console.error('origin/main does not contain the requested release version yet:');
    for (const [file, value] of Object.entries(versions)) {
      console.error(`- ${file}: ${value || '<missing>'}`);
    }
    fail('Push the release version commit to main first, then run publish again.');
  }
}

function pushReleaseTag(version) {
  const tagName = releaseTagName(version);
  git(['fetch', 'origin', 'main', '--tags']);
  ensureTagDoesNotExist(tagName);
  verifyRemoteMainVersion(version);
  git(['tag', '-a', tagName, 'origin/main', '-m', `Study Runner ${version}`]);
  git(['push', 'origin', tagName]);
  return tagName;
}

function printReleaseLinks(tagName) {
  console.log('\nGitHub Actions:');
  console.log('https://github.com/realfabianschmidt/MRG-StudyRunner/actions/workflows/release.yml');
  console.log('\nRelease page:');
  console.log(`https://github.com/realfabianschmidt/MRG-StudyRunner/releases/tag/${tagName}`);
  console.log('\nGitHub Actions will build the installers and publish the release when all platform builds pass.');
}

function prepare(version) {
  const resolvedVersion = resolveNextVersion(version);
  const tagName = releaseTagName(resolvedVersion);

  ensureTagDoesNotExist(tagName);
  ensureDirectReleaseStartingPoint();
  bumpVersions(resolvedVersion);

  if (!flags.has('--skip-checks')) {
    runChecks(resolvedVersion);
  } else {
    console.warn('Skipping checks because --skip-checks was provided.');
  }

  commitVersionChanges(resolvedVersion, `Prepare Study Runner ${resolvedVersion}`);
  console.log(`\nPrepared local version commit for ${resolvedVersion}. No tag was created.`);
}

function publish(version) {
  ensureSemver(version);
  const tagName = pushReleaseTag(version);
  console.log(`\nRelease tag pushed: ${tagName}`);
  printReleaseLinks(tagName);
}

async function release(input) {
  const resolvedVersion = resolveNextVersion(input || 'patch');
  const tagName = releaseTagName(resolvedVersion);

  console.log(`Study Runner release target: ${resolvedVersion}`);
  console.log('Branch: main');
  console.log(`Tag: ${tagName}`);

  if (flags.has('--dry-run')) {
    console.log('\nDry run only. No files, commits, pushes, tags, or releases were changed.');
    console.log('\nA real release would bump versions, run local checks, commit on main, push main, and push the release tag.');
    return;
  }

  ensureTagDoesNotExist(tagName);
  ensureDirectReleaseStartingPoint();
  bumpVersions(resolvedVersion);

  if (!flags.has('--skip-checks')) {
    runChecks(resolvedVersion);
  } else {
    console.warn('Skipping checks because --skip-checks was provided.');
  }

  commitVersionChanges(resolvedVersion, `Release Study Runner ${resolvedVersion}`);
  git(['push', 'origin', 'main']);
  const pushedTag = pushReleaseTag(resolvedVersion);
  console.log(`\nRelease tag pushed: ${pushedTag}`);
  printReleaseLinks(pushedTag);
}

function status() {
  console.log(`Branch: ${currentBranch() || '<detached>'}`);
  console.log(`Git status:\n${git(['status', '--short'], { capture: true }) || '<clean>'}`);
  console.log(`Desktop package version: ${readJson('desktop/package.json').version}`);
  console.log(`Tauri config version: ${readJson('desktop/src-tauri/tauri.conf.json').version}`);
  console.log(`Cargo version: ${versionFromCargoToml(readText('desktop/src-tauri/Cargo.toml'))}`);
}

if (!command || command === '--help' || command === '-h') {
  printHelp();
  process.exit(0);
}

try {
  if (command === 'release') {
    await release(versionInput);
  } else if (command === 'prepare') {
    prepare(versionInput);
  } else if (command === 'publish') {
    publish(versionInput);
  } else if (command === 'status') {
    status();
  } else if (['patch', 'minor', 'major'].includes(command) || /^\d+\.\d+\.\d+$/.test(command)) {
    await release(command);
  } else {
    printHelp();
    fail(`Unknown command: ${command}`);
  }
} catch (error) {
  fail(error instanceof Error ? error.message : String(error));
}
