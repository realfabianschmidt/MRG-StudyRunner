import { copyFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const repoRoot = path.resolve(desktopRoot, '..');
// The editable Python app lives in software/; PyInstaller bundles it into the sidecar.
const softwareRoot = path.join(repoRoot, 'software');
const mode = process.argv.includes('--onedir') ? 'onedir' : 'sidecar';
const isWindows = process.platform === 'win32';
const isMac = process.platform === 'darwin';
const executableName = `study-runner-server${isWindows ? '.exe' : ''}`;
const specFile = mode === 'onedir'
  ? path.join(desktopRoot, 'build_tools/pyinstaller/study_runner_server_onedir.spec')
  : path.join(desktopRoot, 'build_tools/pyinstaller/study_runner_server_sidecar.spec');

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    cwd: softwareRoot,
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

// Run PyInstaller from software/ so study_runner is importable and dist/ lands there.
run('python', ['-m', 'PyInstaller', '--noconfirm', specFile]);

if (mode === 'onedir') {
  console.log('PyInstaller one-folder build is ready in software/dist/study-runner-server.');
  process.exit(0);
}

const tripleResult = spawnSync('rustc', ['--print', 'host-tuple'], {
  cwd: desktopRoot,
  encoding: 'utf8',
});
if (tripleResult.status !== 0 || !tripleResult.stdout.trim()) {
  console.error('Could not determine Rust target triple. Install Rust and run rustc --print host-tuple.');
  process.exit(tripleResult.status || 1);
}

const targetTriple = tripleResult.stdout.trim();
const source = path.join(softwareRoot, 'dist', executableName);
const destinationDir = path.join(desktopRoot, 'src-tauri', 'binaries');
const destination = path.join(destinationDir, `study-runner-server-${targetTriple}${isWindows ? '.exe' : ''}`);
mkdirSync(destinationDir, { recursive: true });
copyFileSync(source, destination);
console.log(`Tauri sidecar copied to ${destination}`);

// On macOS, ad-hoc sign the sidecar so it is allowed to launch (Apple Silicon refuses
// to run unsigned executables). This is a no-cost stand-in for a Developer ID signature;
// it does not remove the Gatekeeper quarantine on a downloaded app, so users of an
// unsigned build must still un-quarantine the app once (see START_HERE.md).
if (isMac) {
  const codesign = spawnSync('codesign', ['--force', '--sign', '-', destination], {
    stdio: 'inherit',
  });
  if (codesign.status !== 0) {
    console.error('Could not ad-hoc codesign the macOS sidecar. Install Xcode command line tools.');
    process.exit(codesign.status || 1);
  }
  console.log('Ad-hoc signed the macOS sidecar.');
}
