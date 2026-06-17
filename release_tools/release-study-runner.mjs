import { readFileSync, writeFileSync } from 'node:fs';
import https from 'node:https';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');
const desktopRoot = path.join(repoRoot, 'desktop_wrapper');
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
  node release_tools/release-study-runner.mjs release <patch|minor|major|version> [--dry-run] [--skip-checks]
  node release_tools/release-study-runner.mjs prepare <patch|minor|major|version> [--skip-checks] [--no-push]
  node release_tools/release-study-runner.mjs publish <version>
  node release_tools/release-study-runner.mjs status

Recommended non-coder command on Windows:
  .\\release.ps1 patch

The release command creates one branch, opens a PR, waits for CI, merges it,
pushes app-v<version>, waits for the release workflow, and verifies latest.json.
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

function gh(args, options = {}) {
  return run('gh', args, options);
}

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
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
  return readJson('desktop_wrapper/package.json').version;
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

function releaseBranchName(version) {
  return `release/study-runner-${version}`;
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

function branchExists(branchName) {
  return git(['rev-parse', '--verify', branchName], {
    capture: true,
    allowFailure: true,
  }).status === 0;
}

function ensureGhReady() {
  const ghVersion = gh(['--version'], {
    capture: true,
    allowFailure: true,
  });
  if (ghVersion.error || ghVersion.status !== 0) {
    fail('GitHub CLI is required for one-command releases. Install it first, then run: gh auth login');
  }

  const authStatus = gh(['auth', 'status'], { allowFailure: true });
  if (authStatus.status !== 0) {
    fail('GitHub CLI is installed but not logged in. Run: gh auth login');
  }
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

function ensureReleaseStartingPoint(branchName) {
  git(['fetch', 'origin', 'main', '--tags']);
  const branch = currentBranch();

  if (branch === branchName) {
    return;
  }

  if (branch !== 'main') {
    fail(`You are on '${branch}'. Switch to main before starting a release.`);
  }

  if (headSha('HEAD') !== headSha('origin/main')) {
    fail('Local main is not equal to origin/main. Pull or reset main before releasing.');
  }

  if (branchExists(branchName)) {
    git(['switch', branchName]);
    return;
  }

  git(['switch', '-c', branchName]);
}

function replacePackageVersion(relativePath, nextVersion) {
  const pkg = readJson(relativePath);
  pkg.version = nextVersion;
  writeJson(relativePath, pkg);
}

function replacePackageLockVersion(nextVersion) {
  const lock = readJson('desktop_wrapper/package-lock.json');
  lock.version = nextVersion;
  if (lock.packages?.['']) {
    lock.packages[''].version = nextVersion;
  }
  writeJson('desktop_wrapper/package-lock.json', lock);
}

function replaceTauriVersion(nextVersion) {
  const config = readJson('desktop_wrapper/src-tauri/tauri.conf.json');
  config.version = nextVersion;
  writeJson('desktop_wrapper/src-tauri/tauri.conf.json', config);
}

function replaceCargoTomlVersion(nextVersion) {
  const relativePath = 'desktop_wrapper/src-tauri/Cargo.toml';
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
  const relativePath = 'desktop_wrapper/src-tauri/Cargo.lock';
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
  replacePackageVersion('desktop_wrapper/package.json', nextVersion);
  replacePackageLockVersion(nextVersion);
  replaceTauriVersion(nextVersion);
  replaceCargoTomlVersion(nextVersion);
  replaceCargoLockVersion(nextVersion);
}

function runChecks(nextVersion) {
  run('node', ['--check', 'desktop_wrapper/web/main.js']);
  run('node', ['--check', 'release_tools/verify-release-version.mjs']);
  run('node', ['release_tools/verify-release-version.mjs', releaseTagName(nextVersion)]);
  run('python', ['-m', 'pytest']);
  run(npmCommand, ['--prefix', 'desktop_wrapper', 'run', 'build:sidecar']);
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

function commitReleaseBranch(version) {
  git(['add', '-A']);
  const files = stagedFiles();
  if (files.length === 0) {
    fail('No changes are staged. Make changes first, or choose a new version.');
  }

  ensureNoStagedSecrets(files);
  git(['commit', '-m', `prepare study runner ${version}`]);
}

function getPr(branchName) {
  const result = gh(['pr', 'view', branchName, '--json', 'number,url,state,headRefOid'], {
    capture: true,
    allowFailure: true,
  });
  if (result.status !== 0 || !result.stdout.trim()) {
    return null;
  }
  return JSON.parse(result.stdout);
}

function ensurePr(version, branchName) {
  const existing = getPr(branchName);
  if (existing?.state === 'OPEN') {
    return existing;
  }

  const body = [
    `Release Study Runner ${version}.`,
    '',
    'This PR was created by the release helper.',
    '',
    'The helper already bumped versions and ran local release checks before pushing this branch.',
  ].join('\n');

  gh([
    'pr',
    'create',
    '--base',
    'main',
    '--head',
    branchName,
    '--title',
    `Release Study Runner ${version}`,
    '--body',
    body,
  ]);

  const pr = getPr(branchName);
  if (!pr) {
    fail('Could not read the created pull request.');
  }
  return pr;
}

function waitForPrChecks(prNumber) {
  gh(['pr', 'checks', String(prNumber), '--watch', '--fail-fast']);
}

function mergePr(pr, expectedHeadSha, version) {
  const latest = gh(['pr', 'view', String(pr.number), '--json', 'headRefOid,state'], { capture: true });
  const latestPr = JSON.parse(latest);
  if (latestPr.state !== 'OPEN') {
    fail(`Pull request #${pr.number} is not open.`);
  }
  if (latestPr.headRefOid !== expectedHeadSha) {
    fail(`Pull request #${pr.number} changed while checks were running. Aborting merge.`);
  }

  gh([
    'pr',
    'merge',
    String(pr.number),
    '--merge',
    '--delete-branch',
    '--subject',
    `Merge Study Runner ${version} release`,
    '--body',
    `Automated release merge for Study Runner ${version}.`,
  ]);
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
    'desktop_wrapper/package.json': readVersionFromGit('origin/main', 'desktop_wrapper/package.json', (content) => JSON.parse(content).version),
    'desktop_wrapper/src-tauri/tauri.conf.json': readVersionFromGit('origin/main', 'desktop_wrapper/src-tauri/tauri.conf.json', (content) => JSON.parse(content).version),
    'desktop_wrapper/src-tauri/Cargo.toml': readVersionFromGit('origin/main', 'desktop_wrapper/src-tauri/Cargo.toml', versionFromCargoToml),
  };

  const mismatches = Object.entries(versions).filter(([, value]) => value !== version);
  if (mismatches.length > 0) {
    console.error('origin/main does not contain the requested release version yet:');
    for (const [file, value] of Object.entries(versions)) {
      console.error(`- ${file}: ${value || '<missing>'}`);
    }
    fail('Merge the release PR first, then run publish again.');
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

function findReleaseRun(tagName) {
  const output = gh([
    'run',
    'list',
    '--workflow',
    'release.yml',
    '--json',
    'databaseId,headBranch,status,conclusion,url',
    '--limit',
    '20',
  ], { capture: true });
  const runs = JSON.parse(output);
  return runs.find((runInfo) => runInfo.headBranch === tagName) || null;
}

function waitForReleaseWorkflow(tagName) {
  let runInfo = null;
  for (let attempt = 0; attempt < 60; attempt += 1) {
    runInfo = findReleaseRun(tagName);
    if (runInfo) break;
    sleep(10000);
  }
  if (!runInfo) {
    fail(`Could not find release workflow run for ${tagName}.`);
  }

  console.log(`Watching release workflow: ${runInfo.url}`);
  gh(['run', 'watch', String(runInfo.databaseId), '--exit-status']);
  return runInfo;
}

function fetchJson(url) {
  return new Promise((resolve, reject) => {
    https
      .get(url, { headers: { 'User-Agent': 'study-runner-release-helper' } }, (response) => {
        let body = '';
        response.setEncoding('utf8');
        response.on('data', (chunk) => {
          body += chunk;
        });
        response.on('end', () => {
          if (response.statusCode < 200 || response.statusCode >= 300) {
            reject(new Error(`HTTP ${response.statusCode} for ${url}`));
            return;
          }
          try {
            resolve(JSON.parse(body));
          } catch (error) {
            reject(error);
          }
        });
      })
      .on('error', reject);
  });
}

async function verifyPublishedRelease(version, tagName) {
  const release = JSON.parse(gh(['release', 'view', tagName, '--json', 'assets,isDraft,isPrerelease,url'], { capture: true }));
  if (release.isDraft) {
    fail(`${tagName} is still a draft release.`);
  }

  const assetNames = release.assets.map((asset) => asset.name);
  const requiredAssets = [
    'latest.json',
    `Study.Runner_${version}_x64-setup.exe`,
    `Study.Runner_${version}_x64-setup.exe.sig`,
    `Study.Runner_${version}_amd64.AppImage`,
    `Study.Runner_${version}_amd64.AppImage.sig`,
    `Study.Runner_${version}_x64.dmg`,
    `Study.Runner_${version}_aarch64.dmg`,
    'Study.Runner_x64.app.tar.gz',
    'Study.Runner_x64.app.tar.gz.sig',
    'Study.Runner_aarch64.app.tar.gz',
    'Study.Runner_aarch64.app.tar.gz.sig',
  ];

  const missingAssets = requiredAssets.filter((asset) => !assetNames.includes(asset));
  if (missingAssets.length > 0) {
    fail(`Release is missing expected assets:\n- ${missingAssets.join('\n- ')}`);
  }

  const latestJson = await fetchJson('https://github.com/realfabianschmidt/MRG-StudyRunner/releases/latest/download/latest.json');
  const requiredPlatforms = ['windows-x86_64', 'linux-x86_64', 'darwin-x86_64', 'darwin-aarch64'];
  const missingPlatforms = requiredPlatforms.filter((platform) => !latestJson.platforms?.[platform]);
  if (latestJson.version !== version || missingPlatforms.length > 0) {
    fail(`latest.json is not ready for ${version}. Missing platforms: ${missingPlatforms.join(', ') || '<none>'}`);
  }

  console.log('\nRelease is published and updater metadata is valid.');
  console.log(release.url);
  console.log('\nInstaller assets:');
  for (const name of assetNames.filter((asset) => !asset.endsWith('.sig') && asset !== 'latest.json' && !asset.endsWith('.tar.gz'))) {
    console.log(`- ${name}`);
  }
}

function prepare(version) {
  const resolvedVersion = resolveNextVersion(version);
  const branchName = releaseBranchName(resolvedVersion);
  const tagName = releaseTagName(resolvedVersion);

  ensureTagDoesNotExist(tagName);
  ensureReleaseStartingPoint(branchName);
  bumpVersions(resolvedVersion);

  if (!flags.has('--skip-checks')) {
    runChecks(resolvedVersion);
  } else {
    console.warn('Skipping checks because --skip-checks was provided.');
  }

  commitReleaseBranch(resolvedVersion);

  if (flags.has('--no-push')) {
    console.log(`\nPrepared local commit on ${branchName}.`);
    return;
  }

  git(['push', '-u', 'origin', branchName]);
  console.log(`\nOpen the PR and merge it after CI passes: https://github.com/realfabianschmidt/MRG-StudyRunner/compare/main...${branchName}?expand=1`);
}

function publish(version) {
  ensureSemver(version);
  const tagName = pushReleaseTag(version);
  console.log(`\nRelease tag pushed: ${tagName}`);
  console.log('Watch the release workflow in GitHub Actions.');
}

async function release(input) {
  const resolvedVersion = resolveNextVersion(input || 'patch');
  const branchName = releaseBranchName(resolvedVersion);
  const tagName = releaseTagName(resolvedVersion);

  console.log(`Study Runner release target: ${resolvedVersion}`);
  console.log(`Branch: ${branchName}`);
  console.log(`Tag: ${tagName}`);

  if (flags.has('--dry-run')) {
    console.log('\nDry run only. No files, branches, PRs, tags, or releases were changed.');
    return;
  }

  ensureGhReady();
  ensureTagDoesNotExist(tagName);
  ensureReleaseStartingPoint(branchName);
  bumpVersions(resolvedVersion);

  if (!flags.has('--skip-checks')) {
    runChecks(resolvedVersion);
  } else {
    console.warn('Skipping checks because --skip-checks was provided.');
  }

  commitReleaseBranch(resolvedVersion);
  const releaseCommit = headSha('HEAD');
  git(['push', '-u', 'origin', branchName]);

  const pr = ensurePr(resolvedVersion, branchName);
  console.log(`Pull Request: ${pr.url}`);
  waitForPrChecks(pr.number);
  mergePr(pr, releaseCommit, resolvedVersion);

  git(['fetch', 'origin', 'main', '--tags']);
  const pushedTag = pushReleaseTag(resolvedVersion);
  waitForReleaseWorkflow(pushedTag);
  await verifyPublishedRelease(resolvedVersion, pushedTag);
}

function status() {
  console.log(`Branch: ${currentBranch() || '<detached>'}`);
  console.log(`Git status:\n${git(['status', '--short'], { capture: true }) || '<clean>'}`);
  console.log(`Desktop package version: ${readJson('desktop_wrapper/package.json').version}`);
  console.log(`Tauri config version: ${readJson('desktop_wrapper/src-tauri/tauri.conf.json').version}`);
  console.log(`Cargo version: ${versionFromCargoToml(readText('desktop_wrapper/src-tauri/Cargo.toml'))}`);
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
