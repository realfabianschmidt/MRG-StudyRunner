const fallbackInfo = {
  adminUrl: 'http://localhost:3000/admin',
  studyUrl: 'http://localhost:3000',
  participantUrl: 'http://localhost:3000',
  dataDir: 'App data folder',
};

const tauri = window.__TAURI__;
let launcherInfo = fallbackInfo;
let updateState = 'idle';
let availableUpdate = null;
let pendingAdminRedirect = false;
let downloadedBytes = 0;
let downloadContentLength = null;

const UPDATE_CHECK_INTERVAL_MS = 30 * 60 * 1000;

function setText(id, value) {
  const element = document.getElementById(id);
  if (element) element.textContent = value;
}

function setReady() {
  document.getElementById('status-dot')?.classList.add('ready');
  setText('status-title', 'Server ready');
  setText('status-detail', 'Opening the admin page. Use the participant link on tablets in the same private network.');
}

function renderInfo(info) {
  launcherInfo = { ...fallbackInfo, ...info };
  setText('admin-url', launcherInfo.adminUrl);
  setText('participant-url', launcherInfo.participantUrl);
  setText('data-dir', launcherInfo.dataDir);
}

function formatBytes(bytes) {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 MB';
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

function renderUpdateState(state, options = {}) {
  updateState = state;

  const panel = document.getElementById('update-panel');
  const button = document.getElementById('btn-install-update');
  const progress = document.getElementById('update-progress');
  const progressBar = document.getElementById('update-progress-bar');

  if (!panel) return;

  panel.hidden = false;
  panel.dataset.state = state;
  if (progress) progress.hidden = true;
  if (progressBar) {
    progressBar.classList.remove('indeterminate');
    progressBar.style.width = '0%';
  }
  if (button) {
    button.hidden = true;
    button.disabled = false;
  }

  if (state === 'checking') {
    setText('update-icon', '...');
    setText('update-title', 'Checking for updates');
    setText('update-detail', 'Looking for a newer Study Runner build.');
    return;
  }

  if (state === 'none') {
    setText('update-icon', 'ok');
    setText('update-title', 'Study Runner is up to date');
    setText('update-detail', `Installed version ${options.currentVersion || 'unknown'} is current.`);
    if (options.silent) {
      panel.hidden = true;
      return;
    }
    window.setTimeout(() => {
      if (updateState === 'none') panel.hidden = true;
    }, 6000);
    return;
  }

  if (state === 'available') {
    setText('update-icon', 'up');
    setText('update-title', `Update ${options.version} available`);
    setText('update-detail', `Installed version ${options.currentVersion || 'unknown'} can be updated now.`);
    if (button) {
      button.hidden = false;
      button.textContent = 'Install Update';
    }
    return;
  }

  if (state === 'downloading') {
    setText('update-icon', 'dl');
    setText('update-title', `Installing update ${availableUpdate?.version || ''}`.trim());
    setText('update-detail', 'Preparing the update download.');
    if (button) {
      button.hidden = false;
      button.disabled = true;
      button.textContent = 'Installing...';
    }
    if (progress) progress.hidden = false;
    return;
  }

  if (state === 'failed') {
    setText('update-icon', '!');
    setText('update-title', 'Update check failed');
    setText('update-detail', options.detail || 'Study Runner could not complete the update workflow.');
    if (button) {
      button.hidden = false;
      button.textContent = 'Check Again';
    }
  }
}

function renderDownloadProgress(event) {
  const progress = document.getElementById('update-progress');
  const progressBar = document.getElementById('update-progress-bar');
  if (!progress || !progressBar) return;

  if (event.event === 'Started') {
    downloadedBytes = 0;
    downloadContentLength = event.data?.contentLength || null;
    progress.hidden = false;
  }

  if (event.event === 'Progress') {
    downloadedBytes += event.data?.chunkLength || 0;
  }

  if (event.event === 'Finished') {
    setText('update-detail', 'Update downloaded. Restarting Study Runner.');
    progressBar.classList.remove('indeterminate');
    progressBar.style.width = '100%';
    return;
  }

  if (downloadContentLength) {
    const percent = Math.min(100, Math.round((downloadedBytes / downloadContentLength) * 100));
    progressBar.classList.remove('indeterminate');
    progressBar.style.width = `${percent}%`;
    setText('update-detail', `${formatBytes(downloadedBytes)} of ${formatBytes(downloadContentLength)} downloaded.`);
    return;
  }

  progressBar.classList.add('indeterminate');
  setText('update-detail', `${formatBytes(downloadedBytes)} downloaded.`);
}

function maybeRedirectToAdmin() {
  if (!pendingAdminRedirect) return;
  if (updateState === 'checking' || updateState === 'available' || updateState === 'downloading') return;

  pendingAdminRedirect = false;
  window.setTimeout(() => {
    window.location.href = launcherInfo.adminUrl;
  }, 450);
}

async function openExternal(url) {
  const openUrl = tauri?.opener?.openUrl || tauri?.opener?.open;
  if (openUrl) {
    await openUrl(url);
    return;
  }
  window.open(url, '_blank', 'noopener');
}

async function checkForUpdate(options = {}) {
  const { silent = false } = options;

  if (!tauri?.core?.invoke || updateState === 'downloading') {
    return;
  }

  if (!silent) {
    renderUpdateState('checking');
  }

  try {
    const update = await tauri.core.invoke('fetch_update');
    availableUpdate = update?.version ? update : null;

    if (availableUpdate) {
      renderUpdateState('available', availableUpdate);
      return;
    }

    renderUpdateState('none', {
      currentVersion: update?.currentVersion,
      silent,
    });
  } catch (error) {
    console.error('[launcher] Could not check for updates:', error);
    if (!silent) {
      renderUpdateState('failed', {
        detail: 'Study Runner could not reach or verify the GitHub release feed.',
      });
    }
  } finally {
    maybeRedirectToAdmin();
  }
}

async function installUpdate() {
  if (updateState === 'failed') {
    await checkForUpdate();
    return;
  }

  if (!availableUpdate) {
    await checkForUpdate();
    return;
  }

  const Channel = tauri?.core?.Channel;
  if (!tauri?.core?.invoke || !Channel) {
    renderUpdateState('failed', {
      detail: 'The Tauri update channel is not available in this window.',
    });
    return;
  }

  downloadedBytes = 0;
  downloadContentLength = null;
  renderUpdateState('downloading');

  const onEvent = new Channel();
  onEvent.onmessage = renderDownloadProgress;

  try {
    await tauri.core.invoke('install_update', { onEvent });
  } catch (error) {
    console.error('[launcher] Could not install update:', error);
    renderUpdateState('failed', {
      detail: 'The update could not be installed. Check the connection and try again.',
    });
  }
}

async function init() {
  if (tauri?.core?.invoke) {
    try {
      renderInfo(await tauri.core.invoke('launcher_info'));
    } catch (error) {
      console.error('[launcher] Could not read launcher info:', error);
      renderInfo(fallbackInfo);
    }
  } else {
    renderInfo(fallbackInfo);
  }

  document.getElementById('btn-open-admin')?.addEventListener('click', () => {
    window.location.href = launcherInfo.adminUrl;
  });
  document.getElementById('btn-open-browser')?.addEventListener('click', () => {
    void openExternal(launcherInfo.adminUrl);
  });
  document.getElementById('btn-install-update')?.addEventListener('click', () => {
    void installUpdate();
  });

  void checkForUpdate();
  window.setInterval(() => {
    void checkForUpdate({ silent: true });
  }, UPDATE_CHECK_INTERVAL_MS);

  if (tauri?.event?.listen) {
    await tauri.event.listen('server-ready', () => {
      setReady();
      pendingAdminRedirect = true;
      maybeRedirectToAdmin();
    });
    await tauri.event.listen('server-output', (event) => {
      if (typeof event.payload === 'string' && event.payload.trim()) {
        setText('status-detail', event.payload.trim());
      }
    });
  }
}

void init();
