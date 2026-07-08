import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(scriptDir, '..');

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
  Git, Node.js, and Python 3.12. Full local checks also need PyInstaller.
  GitHub CLI is not required. GitHub Actions builds the Python update ZIP assets after the tag is pushed.

The release command bumps the Python app version on main, runs fast local checks,
commits, pushes main, then pushes app-v<version> to start the release workflow.
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

function currentPythonVersion() {
  return readPythonVersion(readText('software/study_runner/version.py'));
}

function readPythonVersion(content) {
  const version = content.match(/^__version__\s*=\s*"(?<version>[^"]+)"/m)?.groups?.version;
  if (!version) {
    fail('Could not find __version__ in software/study_runner/version.py.');
  }
  return version;
}

function ensureSemver(value) {
  if (!/^\d+\.\d+\.\d+$/.test(value || '')) {
    fail(`Expected a SemVer version like 0.3.0, got: ${value || '<empty>'}`);
  }
}

function resolveNextVersion(input) {
  const current = currentPythonVersion();
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

function replacePythonVersion(nextVersion) {
  const relativePath = 'software/study_runner/version.py';
  const input = readText(relativePath);
  const output = input.replace(/^__version__\s*=\s*"[^"]+"/m, `__version__ = "${nextVersion}"`);
  if (output === input) {
    fail(`Could not update ${relativePath}.`);
  }
  writeText(relativePath, output);
}

function bumpVersions(nextVersion) {
  replacePythonVersion(nextVersion);
}

function ensureToolchain() {
  const tools = [
    { cmd: 'node', args: ['--version'], hint: 'Install Node.js from https://nodejs.org' },
    { cmd: 'python', args: ['--version'], hint: 'Install Python 3.12 from https://python.org' },
    { cmd: 'git', args: ['--version'], hint: 'Install Git from https://git-scm.com' },
  ];

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
  ensureToolchain();
  run('node', ['--check', 'release_tools/verify-release-version.mjs']);
  run('node', ['--check', 'release_tools/release-study-runner.mjs']);
  run('python', [
    '-m',
    'py_compile',
    'release_tools/package-python-onedir.py',
    'release_tools/write-python-update-key.py',
    'release_tools/build-python-update-manifest.py',
    'release_tools/build-python-onedir.py',
    'release_tools/build-offline-wheelhouse.py',
  ]);
  run('node', ['release_tools/verify-release-version.mjs', releaseTagName(nextVersion)]);
  run('python', ['-m', 'unittest', 'discover', path.join('software', 'tests')]);

  if (fullChecks) {
    run('python', ['release_tools/build-python-onedir.py']);
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
  const secretAssignment = /\b(PYTHON_UPDATER_SIGNING_PRIVATE_KEY|PYTHON_UPDATER_PUBLIC_KEY)\s*=/;

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

function readVersionFromGit(ref, relativePath, reader) {
  const content = git(['show', `${ref}:${relativePath}`], { capture: true });
  return reader(content);
}

function verifyRemoteMainVersion(version) {
  const pythonVersion = readVersionFromGit('origin/main', 'software/study_runner/version.py', readPythonVersion);
  if (pythonVersion !== version) {
    console.error('origin/main does not contain the requested release version yet:');
    console.error(`- software/study_runner/version.py: ${pythonVersion || '<missing>'}`);
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
  console.log('\nGitHub Actions will build and publish the Python update ZIPs when all platform builds pass.');
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
    console.log('\nA real release would bump the Python app version, run local checks, commit on main, push main, and push the release tag.');
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
  console.log(`Python app version: ${currentPythonVersion()}`);
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
