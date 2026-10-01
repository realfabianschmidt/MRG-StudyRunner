/**
 * Which software produced a session (meta/manifest.json -> provenance.software,
 * see runtime_core/delivery/software_provenance.py): the rows the session view
 * lists and the sentence a methods section cites.
 */
const ROLE_LABELS = {
  recording: ['sessions.software.role.recording', 'Recording'],
  card: ['sessions.software.role.card', 'Cards'],
  destination: ['sessions.software.role.destination', 'Upload'],
};
// The methods text names the plugins that produced data, sensors first;
// upload destinations only copy finished files and are listed, not cited.
const CITED_ROLES = ['recording', 'card'];

const fallbackOnly = (_key, fallback) => fallback;

// Sorted by key: the server's JSON does not keep the stored order.
function pluginsWithRole(software, role) {
  return Object.entries(software?.plugins || {})
    .filter(([, plugin]) => plugin?.role === role)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, plugin]) => `${key} ${plugin.version}`);
}

export function softwareRows(software, translate = fallbackOnly) {
  if (!software || typeof software !== 'object') return [];
  const rows = [{
    label: 'Study Runner',
    value: software.study_runner_version || translate('sessions.software.notRecorded', 'not recorded'),
  }];
  for (const [role, [key, fallback]] of Object.entries(ROLE_LABELS)) {
    const plugins = pluginsWithRole(software, role);
    if (plugins.length) rows.push({ label: translate(key, fallback), value: plugins.join(', ') });
  }
  return rows;
}

/** Why the rows are incomplete, or '' when they are not. */
export function softwareNote(software, translate = fallbackOnly) {
  if (!software || typeof software !== 'object') {
    return translate('sessions.software.missing', 'Not recorded: this session is older than version tracking in Study Runner.');
  }
  if (software.partial) {
    return translate('sessions.software.partial', 'Recorded before version tracking: only the sensor plugin versions are known.');
  }
  return '';
}

/** One sentence for a methods section, or '' when the Study Runner version is unknown. */
export function methodsText(software, translate = fallbackOnly) {
  const version = software?.study_runner_version;
  if (!version) return '';
  const plugins = CITED_ROLES.flatMap((role) => pluginsWithRole(software, role));
  if (!plugins.length) {
    return translate('sessions.software.methods', 'Data were recorded with Study Runner {version}.')
      .replace('{version}', version);
  }
  return translate('sessions.software.methodsWithPlugins', 'Data were recorded with Study Runner {version} (plugins: {plugins}).')
    .replace('{version}', version)
    .replace('{plugins}', plugins.join(', '));
}
