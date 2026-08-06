const DEFAULT_LOAD_TIMEOUT_MS = 2000;

function errorMessage(error) {
  if (error instanceof Error && error.message) return error.message;
  return String(error || 'Unknown participant extension error');
}

function cleanupFromResult(result) {
  if (typeof result === 'function') return result;
  if (result && typeof result.cleanup === 'function') return result.cleanup;
  return null;
}

function withTimeout(promise, timeoutMs, message) {
  let timer = null;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([Promise.resolve(promise), timeout])
    .finally(() => clearTimeout(timer));
}

/**
 * Manage optional, manifest-declared participant UI extensions.
 *
 * Extensions are trusted application assets, but remain optional presentation
 * adapters. A missing, slow, or broken extension must never delay a stimulus,
 * navigation, or submission. Every lifecycle call is therefore invoked in an
 * isolated task and failures are exposed through the heartbeat status map.
 */
export class ParticipantPluginExtensionManager {
  constructor(options) {
    this.getPlugins = options.getPlugins;
    this.isEnabled = options.isEnabled;
    this.loadExtensions = options.loadExtensions;
    this.getExtensionModule = options.getExtensionModule;
    this.createContext = options.createContext;
    this.onWarning = typeof options.onWarning === 'function' ? options.onWarning : () => {};
    this.loadTimeoutMs = Math.max(100, Number(options.loadTimeoutMs || DEFAULT_LOAD_TIMEOUT_MS));
    this.instances = new Map();
    this.statuses = new Map();
    this.failedFactories = new Set();
    this.monitorsRequested = new Set();
    this.extensionsLoaded = false;
    this.loading = null;
  }

  reportStatus(pluginKey, status) {
    const key = String(pluginKey || '').trim();
    if (!key || !status || typeof status !== 'object' || Array.isArray(status)) return;
    const previous = this.statuses.get(key) || {};
    this.statuses.set(key, { ...previous, ...status });
  }

  _warn(pluginKey, hook, error) {
    const message = errorMessage(error);
    this.reportStatus(pluginKey, {
      state: 'warning',
      last_error: message,
      failed_hook: hook,
    });
    this.onWarning({ pluginKey, hook, message, error });
  }

  async _ensureLoaded() {
    if (this.extensionsLoaded) return;
    if (!this.loading) {
      const pending = Promise.resolve().then(() => this.loadExtensions());
      this.loading = withTimeout(
        pending,
        this.loadTimeoutMs,
        'Participant plugin extensions did not load in time.',
      )
        .then(() => { this.extensionsLoaded = true; })
        .finally(() => { this.loading = null; });
    }
    await this.loading;
  }

  async sync(runtimePayload = {}) {
    try {
      await this._ensureLoaded();
    } catch (error) {
      this._warn('participant_extensions', 'load', error);
      return;
    }

    const plugins = Array.isArray(this.getPlugins?.()) ? this.getPlugins() : [];
    const declaredKeys = new Set();

    for (const plugin of plugins) {
      const pluginKey = String(plugin?.plugin_key || '').trim();
      if (!pluginKey) continue;
      const declaredAsset = plugin?.ui?.extensions?.participant;
      if (!declaredAsset) continue;
      declaredKeys.add(pluginKey);

      if (!this.isEnabled(plugin)) {
        await this._deactivate(pluginKey, 'disabled');
        this.failedFactories.delete(pluginKey);
        this.reportStatus(pluginKey, { enabled: false, state: 'disabled', last_error: '' });
        continue;
      }

      const module = this.getExtensionModule(plugin);
      if (!module || typeof module.createParticipantExtension !== 'function') {
        if (!this.failedFactories.has(pluginKey)) {
          this.failedFactories.add(pluginKey);
          this._warn(pluginKey, 'load', new Error(`Participant extension ${pluginKey} is unavailable.`));
        }
        continue;
      }

      if (!this.instances.has(pluginKey) && !this.failedFactories.has(pluginKey)) {
        const baseContext = this.createContext(plugin) || {};
        const context = Object.freeze({
          ...baseContext,
          plugin,
          reportStatus: (status) => this.reportStatus(pluginKey, status),
        });
        try {
          const pending = Promise.resolve(module.createParticipantExtension(context));
          let instance;
          try {
            instance = await withTimeout(
              pending,
              this.loadTimeoutMs,
              `Participant extension ${pluginKey} did not initialize in time.`,
            );
          } catch (error) {
            pending.then((lateInstance) => {
              if (typeof lateInstance?.dispose === 'function') {
                try {
                  lateInstance.dispose({ reason: 'initialization_timeout' });
                } catch {
                  // The initialization warning is already recorded.
                }
              }
            }).catch(() => {});
            throw error;
          }
          if (!instance || typeof instance !== 'object') {
            throw new Error(`Participant extension ${pluginKey} returned no lifecycle object.`);
          }
          this.instances.set(pluginKey, instance);
          this.reportStatus(pluginKey, { enabled: true, state: 'ready', last_error: '' });
        } catch (error) {
          this.failedFactories.add(pluginKey);
          this._warn(pluginKey, 'createParticipantExtension', error);
          continue;
        }
      }

      this._invoke(pluginKey, 'onRuntimeChange', {
        ...runtimePayload,
        enabled: true,
      });
    }

    for (const pluginKey of [...this.instances.keys()]) {
      if (!declaredKeys.has(pluginKey)) {
        await this._deactivate(pluginKey, 'removed');
      }
    }
  }

  _invoke(pluginKey, hook, payload) {
    const instance = this.instances.get(pluginKey);
    const callback = instance?.[hook];
    if (typeof callback !== 'function') return Promise.resolve(undefined);

    try {
      const result = callback.call(instance, payload);
      return Promise.resolve(result).catch((error) => {
        this._warn(pluginKey, hook, error);
        return undefined;
      });
    } catch (error) {
      this._warn(pluginKey, hook, error);
      return Promise.resolve(undefined);
    }
  }

  startPrestudyMonitors(payload = {}) {
    for (const pluginKey of this.instances.keys()) {
      if (this.monitorsRequested.has(pluginKey)) continue;
      this.monitorsRequested.add(pluginKey);
      void this._invoke(pluginKey, 'startPrestudyMonitor', payload);
    }
  }

  stopPrestudyMonitors(payload = {}) {
    for (const pluginKey of this.instances.keys()) {
      if (!this.monitorsRequested.has(pluginKey)) continue;
      this.monitorsRequested.delete(pluginKey);
      void this._invoke(pluginKey, 'stopPrestudyMonitor', payload);
    }
  }

  startStimulus(payload = {}) {
    const lifecycle = { closed: false, cleanups: [] };
    for (const pluginKey of this.instances.keys()) {
      void this._invoke(pluginKey, 'startStimulus', payload).then((result) => {
        const cleanup = cleanupFromResult(result);
        if (!cleanup) return;
        if (lifecycle.closed) {
          try {
            cleanup();
          } catch (error) {
            this._warn(pluginKey, 'stopStimulus', error);
          }
          return;
        }
        lifecycle.cleanups.push({ pluginKey, cleanup });
      });
    }

    return () => {
      if (lifecycle.closed) return;
      lifecycle.closed = true;
      for (const { pluginKey, cleanup } of lifecycle.cleanups.splice(0)) {
        try {
          cleanup();
        } catch (error) {
          this._warn(pluginKey, 'stopStimulus', error);
        }
      }
    };
  }

  beforeSubmit(payload = {}) {
    this.stopPrestudyMonitors({ ...payload, reason: 'submit' });
    for (const pluginKey of this.instances.keys()) {
      void this._invoke(pluginKey, 'beforeSubmit', payload);
    }
  }

  onSubmitFailed(payload = {}) {
    for (const pluginKey of this.instances.keys()) {
      void this._invoke(pluginKey, 'onSubmitFailed', payload);
    }
    this.startPrestudyMonitors({ ...payload, reason: 'submit_failed' });
  }

  heartbeatStatus() {
    const result = {};
    for (const [pluginKey, status] of this.statuses.entries()) {
      result[pluginKey] = { ...status };
    }
    for (const [pluginKey, instance] of this.instances.entries()) {
      const callback = instance?.getHeartbeatStatus;
      if (typeof callback !== 'function') continue;
      try {
        const status = callback.call(instance);
        if (status && typeof status === 'object' && !Array.isArray(status)) {
          result[pluginKey] = { ...(result[pluginKey] || {}), ...status };
        }
      } catch (error) {
        this._warn(pluginKey, 'getHeartbeatStatus', error);
        result[pluginKey] = { ...(this.statuses.get(pluginKey) || {}) };
      }
    }
    return result;
  }

  async _deactivate(pluginKey, reason) {
    const instance = this.instances.get(pluginKey);
    if (!instance) return;
    this.monitorsRequested.delete(pluginKey);
    await this._invoke(pluginKey, 'stopPrestudyMonitor', { reason });
    await this._invoke(pluginKey, 'dispose', { reason });
    this.instances.delete(pluginKey);
  }

  async dispose(reason = 'page_dispose') {
    for (const pluginKey of [...this.instances.keys()]) {
      await this._deactivate(pluginKey, reason);
    }
  }
}

export function createParticipantPluginExtensionManager(options) {
  return new ParticipantPluginExtensionManager(options);
}
