import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopRoot, '..');
const tauriRoot = path.join(desktopRoot, 'src-tauri');
const isWindows = process.platform === 'win32';
const npmCommand = isWindows ? 'npm.cmd' : 'npm';

const command = process.argv[2];
const version = process.argv[3];
const flags = new Set(process.argv.slice(4));

function printHelp() {
  console.log(`
Study Runner release helper

Usage:
  node desktop_app/scripts/release-study-runner.mjs prepare <version> [--skip-checks] [--no-push]
  node desktop_app/scripts/release-study-runner.mjs publish <version>
  node desktop_app/scripts/release-study-runner.mjs status

Recommended flow:
  1. Make your Study Runner changes.
  2. Run: node desktop_app/scripts/release-study-runner.mjs prepare 0.2.3
  3. Open and merge the printed Pull Request URL.
  4. Run: node desktop_app/scripts/release-study-runner.mjs publish 0.2.3

The helper uses one branch per version: release/study-runner-<version>.
`);
}

function fail(message) {
  console.error(`\n${message}`);
  process.exit(1);
}

function run(cmd, args, options = {}) {
  const capture = options.capture || false;
  const result = spawnSync(cmd, args, {
    cwd: options.cwd || repoRoot,
    stdio: capture ? 'pipe' : 'inherit',
    encoding: capture ? 'utf8' : undefined,
    shell: false,
    maxBuffer: 50 * 1024 * 1024,
  });

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

function ensureSemver(value) {
  if (!/^\d+\.\d+\.\d+$/.test(value || '')) {
    fail(`Expected a SemVer version like 0.2.3, got: ${value || '<empty>'}`);
  }
}

function releaseBranchName(value) {
  return `release/study-runner-${value}`;
}

function releaseTagName(value) {
  return `app-v${value}`;
}

function remoteCompareUrl(branchName) {
  const remote = git(['remote', 'get-url', 'origin'], { capture: true });
  const match = remote.match(/github\.com[:/](?<owner>[^/]+)\/(?<repo>[^/.]+)(?:\.git)?$/);
  if (!match?.groups) {
    return '<could not derive GitHub compare URL from origin remote>';
  }
  return `https://github.com/${match.groups.owner}/${match.groups.repo}/compare/main...${encodeURIComponent(branchName)}?expand=1`;
}

function currentBranch() {
  return git(['branch', '--show-current'], { capture: true });
}

function branchExists(branchName) {
  const result = git(['rev-parse', '--verify', branchName], {
    capture: true,
    allowFailure: true,
  });
  return result.status === 0;
}

function ensureReleaseBranch(targetBranch) {
  const branch = currentBranch();
  if (branch === targetBranch) {
    return;
  }

  if (branch !== 'main' && branch !== 'master') {
    fail(`You are on '${branch}'. Switch to main first, or to '${targetBranch}' if it already exists.`);
  }

  if (branchExists(targetBranch)) {
    git(['switch', targetBranch]);
    return;
  }

  git(['switch', '-c', targetBranch]);
}

function ensureTagDoesNotExist(tagName) {
  const local = git(['rev-parse', '--verify', tagName], {
    capture: true,
    allowFailure: true,
  });
  if (local.status === 0) {
    fail(`Local tag already exists: ${tagName}`);
  }

  const remote = git(['ls-remote', '--tags', 'origin', tagName], { capture: true });
  if (remote) {
    fail(`Remote tag already exists: ${tagName}`);
  }
}

function replacePackageVersion(relativePath, nextVersion) {
  const pkg = readJson(relativePath);
  pkg.version = nextVersion;
  writeJson(relativePath, pkg);
}

function replacePackageLockVersion(nextVersion) {
  const lock = readJson('desktop_app/package-lock.json');
  lock.version = nextVersion;
  if (lock.packages?.['']) {
    lock.packages[''].version = nextVersion;
  }
  writeJson('desktop_app/package-lock.json', lock);
}

function replaceTauriVersion(nextVersion) {
  const config = readJson('desktop_app/src-tauri/tauri.conf.json');
  config.version = nextVersion;
  writeJson('desktop_app/src-tauri/tauri.conf.json', config);
}

function replaceCargoTomlVersion(nextVersion) {
  const relativePath = 'desktop_app/src-tauri/Cargo.toml';
  const input = readText(relativePath);
  const output = input.replace(
    /(^\[package\][\s\S]*?^version\s*=\s*")[^"]+(")/m,
    `$1${nextVersion}$2`,
  );
  if (output === input) {
    fail('Could not update [package] version in desktop_app/src-tauri/Cargo.toml.');
  }
  writeText(relativePath, output);
}

function replaceCargoLockVersion(nextVersion) {
  const relativePath = 'desktop_app/src-tauri/Cargo.lock';
  const input = readText(relativePath);
  const output = input.replace(
    /(\[\[package\]\]\r?\nname = "study-runner-desktop"\r?\nversion = ")[^"]+(")/,
    `$1${nextVersion}$2`,
  );
  if (output === input) {
    fail('Could not update study-runner-desktop version in desktop_app/src-tauri/Cargo.lock.');
  }
  writeText(relativePath, output);
}

function bumpVersions(nextVersion) {
  replacePackageVersion('desktop_app/package.json', nextVersion);
  replacePackageLockVersion(nextVersion);
  replaceTauriVersion(nextVersion);
  replaceCargoTomlVersion(nextVersion);
  replaceCargoLockVersion(nextVersion);
}

function runChecks(nextVersion) {
  run('node', ['--check', 'desktop_app/web/main.js']);
  run('node', ['--check', 'desktop_app/scripts/verify-release-version.mjs']);
  run('node', ['desktop_app/scripts/verify-release-version.mjs', releaseTagName(nextVersion)]);
  run('python', ['-m', 'pytest']);
  run(npmCommand, ['--prefix', 'desktop_app', 'run', 'build:sidecar']);
  run('cargo', ['check', '-q'], { cwd: tauriRoot });
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

function commitAndMaybePush(nextVersion, targetBranch) {
  git(['add', '-A']);
  const files = stagedFiles();
  if (files.length === 0) {
    fail('No changes are staged. Make changes first, or choose a new version.');
  }

  ensureNoStagedSecrets(files);

  git(['commit', '-m', `prepare study runner ${nextVersion}`]);

  if (flags.has('--no-push')) {
    console.log(`\nPrepared local commit on ${targetBranch}. Push it when ready:`);
    console.log(`  git push -u origin ${targetBranch}`);
    return;
  }

  git(['push', '-u', 'origin', targetBranch]);
  console.log('\nOpen this Pull Request URL and merge it after CI passes:');
  console.log(`  ${remoteCompareUrl(targetBranch)}`);
  console.log('\nAfter the PR is merged, publish the release tag with:');
  console.log(`  node desktop_app/scripts/release-study-runner.mjs publish ${nextVersion}`);
}

function readVersionFromGit(ref, relativePath, reader) {
  const content = git(['show', `${ref}:${relativePath}`], { capture: true });
  return reader(content);
}

function versionFromCargoToml(content) {
  const packageSection = content.match(/^\[package\]$(?<body>[\s\S]*?)(?=^\[|$)/m);
  const body = packageSection?.groups?.body || content;
  return body.match(/^version\s*=\s*"(?<version>[^"]+)"/m)?.groups?.version;
}

function verifyRemoteMainVersion(nextVersion) {
  const versions = {
    'desktop_app/package.json': readVersionFromGit('origin/main', 'desktop_app/package.json', (content) => JSON.parse(content).version),
    'desktop_app/src-tauri/tauri.conf.json': readVersionFromGit('origin/main', 'desktop_app/src-tauri/tauri.conf.json', (content) => JSON.parse(content).version),
    'desktop_app/src-tauri/Cargo.toml': readVersionFromGit('origin/main', 'desktop_app/src-tauri/Cargo.toml', versionFromCargoToml),
  };

  const mismatches = Object.entries(versions).filter(([, value]) => value !== nextVersion);
  if (mismatches.length > 0) {
    console.error('origin/main does not contain the requested release version yet:');
    for (const [file, value] of Object.entries(versions)) {
      console.error(`- ${file}: ${value || '<missing>'}`);
    }
    fail('Merge the release PR first, then run publish again.');
  }
}

function prepare(nextVersion) {
  ensureSemver(nextVersion);
  const branchName = releaseBranchName(nextVersion);
  const tagName = releaseTagName(nextVersion);

  ensureTagDoesNotExist(tagName);
  ensureReleaseBranch(branchName);
  bumpVersions(nextVersion);

  if (!flags.has('--skip-checks')) {
    runChecks(nextVersion);
  } else {
    console.warn('Skipping checks because --skip-checks was provided.');
  }

  commitAndMaybePush(nextVersion, branchName);
}

function publish(nextVersion) {
  ensureSemver(nextVersion);
  const tagName = releaseTagName(nextVersion);

  git(['fetch', 'origin', 'main', '--tags']);
  ensureTagDoesNotExist(tagName);
  verifyRemoteMainVersion(nextVersion);
  git(['tag', '-a', tagName, 'origin/main', '-m', `Study Runner ${nextVersion}`]);
  git(['push', 'origin', tagName]);

  console.log('\nRelease tag pushed. Watch the workflow here:');
  console.log('  https://github.com/realfabianschmidt/MRG-StudyRunner/actions/workflows/release.yml');
}

function status() {
  console.log(`Branch: ${currentBranch() || '<detached>'}`);
  console.log(`Git status:\n${git(['status', '--short'], { capture: true }) || '<clean>'}`);
  console.log(`Desktop package version: ${readJson('desktop_app/package.json').version}`);
  console.log(`Tauri config version: ${readJson('desktop_app/src-tauri/tauri.conf.json').version}`);
  console.log(`Cargo version: ${versionFromCargoToml(readText('desktop_app/src-tauri/Cargo.toml'))}`);
}

if (!command || command === '--help' || command === '-h') {
  printHelp();
  process.exit(0);
}

if (command === 'prepare') {
  prepare(version);
} else if (command === 'publish') {
  publish(version);
} else if (command === 'status') {
  status();
} else {
  printHelp();
  fail(`Unknown command: ${command}`);
}
