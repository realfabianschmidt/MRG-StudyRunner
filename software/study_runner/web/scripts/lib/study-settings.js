/**
 * The one client-side definition of a study's settings.
 *
 * This shape used to be written out three times - in admin-controller.js, in
 * notion-settings-controller.js, and (authoritatively) in the backend's
 * _validate_study_settings(). The copies drifted, and the drift cost real data:
 * a save from the study-settings modal rebuilt the object without the Nextcloud
 * keys, so configuring Nextcloud and later toggling the progress bar silently
 * switched the upload off and dropped the share link.
 *
 * Every field below mirrors backend/services/validation.py::_validate_study_settings.
 * Keep the two in step - tests/test_study_settings_contract.py fails otherwise.
 *
 * Like the backend, normalizeStudySettings() returns a fresh object holding
 * exactly the known keys. Unknown keys are dropped rather than passed through,
 * because the backend drops them too - letting them survive here would only
 * hide a mismatch until the next save.
 */
import { pluginsWithCapability } from './plugin-catalog.js';

/** Build sensor defaults exclusively from the currently loaded plugin catalog. */
export function defaultStudySensors(sensorsEnabled = true) {
  return Object.fromEntries(pluginsWithCapability('study_sensor').map((plugin) => {
    const capability = plugin.capability_config?.study_sensor || {};
    return [plugin.plugin_key, Boolean(sensorsEnabled && capability.default_enabled)];
  }));
}

export function defaultStudySettings() {
  return {
    sensors_enabled: true,
    sensors: defaultStudySensors(true),
    plugins: defaultStudyPlugins(true),
    progress_bar_enabled: false,
  };
}

/** Mirrors backend normalize_study_sensors(): the master switch wins over each sensor. */
export function normalizeStudySensors(settings, sensorsEnabled) {
  const rawSensors = settings?.sensors && typeof settings.sensors === 'object'
    && !Array.isArray(settings.sensors)
    ? settings.sensors
    : {};
  const defaults = defaultStudySensors(sensorsEnabled);
  if (!sensorsEnabled) {
    return Object.fromEntries(
      [...new Set([...Object.keys(defaults), ...Object.keys(rawSensors)])]
        .map((pluginKey) => [pluginKey, false]),
    );
  }
  return {
    ...defaults,
    ...Object.fromEntries(
      Object.entries(rawSensors).map(([pluginKey, enabled]) => [pluginKey, Boolean(enabled)]),
    ),
  };
}

export function normalizeStudySettings(settings) {
  const source = settings && typeof settings === 'object' ? settings : {};
  const sensorsEnabled = source.sensors_enabled !== false;
  const legacySensors = normalizeStudySensors(source, sensorsEnabled);
  const plugins = syncLegacyFieldsToPlugins(source, legacySensors);
  const sensors = Object.fromEntries(pluginsWithCapability('study_sensor').map((plugin) => [
    plugin.plugin_key,
    Boolean(plugins[plugin.plugin_key]?.enabled),
  ]));
  return {
    sensors_enabled: Object.values(sensors).some(Boolean),
    sensors: sensors,
    plugins: plugins,
    progress_bar_enabled: Boolean(source.progress_bar_enabled),
  };
}

/** Preserve API-v3 plugin selections without knowing plugin keys in the UI. */
export function normalizeStudyPlugins(plugins) {
  if (!plugins || typeof plugins !== 'object' || Array.isArray(plugins)) {
    return {};
  }
  return Object.fromEntries(
    Object.entries(plugins)
      .filter(([key, entry]) => key && entry && typeof entry === 'object' && !Array.isArray(entry))
      .map(([key, entry]) => [key, {
        enabled: Boolean(entry.enabled),
        required: Boolean(entry.required),
        settings: entry.settings && typeof entry.settings === 'object' && !Array.isArray(entry.settings)
          ? { ...entry.settings }
          : {},
      }]),
  );
}

/**
 * Keep the transitional legacy controls and the v3 object consistent.
 * Plugin names are discovered from the object; this bridge does not own a
 * second list of integrations.
 */
function syncLegacyFieldsToPlugins(source, sensors) {
  const plugins = normalizeStudyPlugins(source.plugins);

  pluginsWithCapability('study_sensor').forEach((plugin) => {
    const key = plugin.plugin_key;
    const capability = plugin.capability_config?.study_sensor || {};
    const previous = plugins[key] || {};
    const rawSelection = source.plugins?.[key];
    const hasCanonical = rawSelection && typeof rawSelection === 'object'
      && !Array.isArray(rawSelection);
    const hasRequired = rawSelection && Object.prototype.hasOwnProperty.call(rawSelection, 'required');
    plugins[key] = {
      enabled: hasCanonical ? Boolean(rawSelection.enabled) : Boolean(sensors[key]),
      required: hasRequired ? Boolean(rawSelection.required) : capability.default_required !== false,
      settings: { ...(previous.settings || {}) },
    };
  });
  pluginsWithCapability('upload_destination').forEach((plugin) => {
    const key = plugin.plugin_key;
    if (plugins[key]) return;
    const capability = plugin.capability_config?.upload_destination || {};
    const legacy = capability.legacy || {};
    const enabledField = String(legacy.enabled_field || '');
    const legacySettings = legacy.settings && typeof legacy.settings === 'object'
      ? legacy.settings
      : {};
    const destinationSettings = Object.fromEntries(Object.entries(legacySettings)
      .filter(([, legacyField]) => Object.prototype.hasOwnProperty.call(source, legacyField))
      .map(([name, legacyField]) => [name, typeof source[legacyField] === 'string'
        ? source[legacyField].trim()
        : source[legacyField]]));
    plugins[key] = {
      enabled: enabledField && Object.prototype.hasOwnProperty.call(source, enabledField)
        ? Boolean(source[enabledField])
        : Boolean(capability.default_enabled),
      required: Boolean(capability.default_required),
      settings: destinationSettings,
    };
  });

  Object.entries(sensors).forEach(([pluginKey, enabled]) => {
    if (!plugins[pluginKey]) {
      plugins[pluginKey] = { enabled: Boolean(enabled), required: true, settings: {} };
      return;
    }
    plugins[pluginKey] = { ...plugins[pluginKey], enabled: Boolean(enabled) };
  });

  return plugins;
}

function defaultStudyPlugins(sensorsEnabled) {
  const sensors = defaultStudySensors(sensorsEnabled);
  return syncLegacyFieldsToPlugins({ plugins: {} }, sensors);
}
