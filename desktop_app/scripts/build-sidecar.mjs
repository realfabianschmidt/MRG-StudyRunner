import { copyFileSync, mkdirSync } from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopRoot = path.resolve(scriptDir, '..');
const softwareRoot = path.resolve(desktopRoot, '..');
const mode = process.argv.includes('--onedir') ? 'onedir' : 'sidecar';
const isWindows = process.platform === 'win32';
const executableName = `study-runner-server${isWindows ? '.exe' : ''}`;
const specFile = mode === 'onedir'
  ? 'packaging/study_runner_server_onedir.spec'
  : 'packaging/study_runner_server_sidecar.spec';

function run(command, args, options = {}) {
  const result = spawnSync(command, args, {
    stdio: 'inherit',
    cwd: softwareRoot,
    shell: isWindows,
    ...options,
  });
  if (result.status !== 0) {
    process.exit(result.status || 1);
  }
}

run('python', ['-m', 'PyInstaller', '--noconfirm', specFile]);

if (mode === 'onedir') {
  console.log('PyInstaller one-folder build is ready in Software/dist/study-runner-server.');
  process.exit(0);
}

const tripleResult = spawnSync('rustc', ['--print', 'host-tuple'], {
  cwd: desktopRoot,
  encoding: 'utf8',
  shell: isWindows,
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
