// Event provenance is composed once for cards, stimuli and marker commands.
export function createEventPayloadBuilder(context) {
  const { state, getStudyClientId, resolveParticipantId, getClockEvidence,
    getClientClockOffsetMs, getTrialPluginFields } = context;
  return (questionIndex, question, phase, clientTriggerMs = performance.now()) => {
    const clock = getClockEvidence(clientTriggerMs);
    return {
      study_id: state.config.study_id || '',
      session_id: state.sessionId || '',
      client_id: getStudyClientId(),
      participant_id: resolveParticipantId(),
      question_index: Number.isInteger(questionIndex) ? questionIndex : null,
      question_type: question?.type || '',
      phase,
      client_trigger_ms: clientTriggerMs,
      client_trigger_epoch_ms: clock.source_epoch_ms,
      clock_offset_ms: clock.time_source === 'tablet_sync' ? getClientClockOffsetMs() : null,
      clock_sync_id: clock.clock_sync_id,
      clock_sync_age_ms: clock.clock_sync_age_ms,
      clock_sync_rtt_ms: clock.clock_sync_rtt_ms,
      time_source: clock.time_source,
      ...getTrialPluginFields(question),
    };
  };
}
