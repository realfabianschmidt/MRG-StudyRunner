#ifndef STUDY_RUNNER_XDF_CORE_H
#define STUDY_RUNNER_XDF_CORE_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32)
#define SR_XDF_EXPORT __declspec(dllexport)
#else
#define SR_XDF_EXPORT __attribute__((visibility("default")))
#endif

#ifdef __cplusplus
extern "C" {
#endif

#define SR_XDF_CORE_ABI_VERSION 1u

typedef struct sr_xdf_writer sr_xdf_writer;

enum sr_xdf_status {
    SR_XDF_OK = 0,
    SR_XDF_INVALID_ARGUMENT = 1,
    SR_XDF_IO_ERROR = 2,
    SR_XDF_INVALID_STATE = 3,
    SR_XDF_UNSUPPORTED = 4,
    SR_XDF_CORRUPT_SOURCE = 5,
    SR_XDF_INTERNAL_ERROR = 255
};

enum sr_xdf_value_format {
    SR_XDF_INT8 = 1,
    SR_XDF_INT16 = 2,
    SR_XDF_INT32 = 3,
    SR_XDF_INT64 = 4,
    SR_XDF_FLOAT32 = 5,
    SR_XDF_FLOAT64 = 6
};

enum sr_xdf_chunk_tag {
    SR_XDF_FILE_HEADER = 1,
    SR_XDF_STREAM_HEADER = 2,
    SR_XDF_SAMPLES = 3,
    SR_XDF_CLOCK_OFFSET = 4,
    SR_XDF_BOUNDARY = 5,
    SR_XDF_STREAM_FOOTER = 6
};

typedef struct sr_xdf_merge_report {
    uint32_t abi_version;
    uint32_t source_count;
    uint32_t stream_count;
    uint32_t reserved;
    uint64_t copied_chunk_count;
    uint64_t copied_payload_bytes;
} sr_xdf_merge_report;

SR_XDF_EXPORT uint32_t sr_xdf_core_abi_version(void);
SR_XDF_EXPORT const char *sr_xdf_core_probe_json(void);
/* Returns the full byte count excluding NUL and always NUL-terminates capacity > 0. */
SR_XDF_EXPORT uint64_t sr_xdf_copy_last_error(char *buffer, uint64_t capacity);

/* Opens a new file. Existing targets are rejected; parents are never created. */
SR_XDF_EXPORT int sr_xdf_writer_open_exclusive(
    const char *utf8_path, sr_xdf_writer **out_writer);

SR_XDF_EXPORT int sr_xdf_writer_write_stream_header(
    sr_xdf_writer *writer, uint32_t stream_id, const uint8_t *xml, uint64_t xml_size);

SR_XDF_EXPORT int sr_xdf_writer_write_stream_footer(
    sr_xdf_writer *writer, uint32_t stream_id, const uint8_t *xml, uint64_t xml_size);

SR_XDF_EXPORT int sr_xdf_writer_write_numeric_samples(
    sr_xdf_writer *writer,
    uint32_t stream_id,
    const double *timestamps,
    uint64_t timestamp_count,
    const void *values,
    uint64_t value_count,
    uint32_t channel_count,
    uint32_t value_format);

/* Offsets has value_count + 1 entries and must end at packed_utf8_size. */
SR_XDF_EXPORT int sr_xdf_writer_write_string_samples(
    sr_xdf_writer *writer,
    uint32_t stream_id,
    const double *timestamps,
    uint64_t timestamp_count,
    const uint8_t *packed_utf8,
    uint64_t packed_utf8_size,
    const uint64_t *offsets,
    uint64_t offset_count,
    uint32_t channel_count);

SR_XDF_EXPORT int sr_xdf_writer_write_clock_offset(
    sr_xdf_writer *writer, uint32_t stream_id, double collection_time, double offset);
SR_XDF_EXPORT int sr_xdf_writer_write_boundary(sr_xdf_writer *writer);

/* Checked raw payload append for the merger; file-header chunks are rejected. */
SR_XDF_EXPORT int sr_xdf_writer_write_raw_chunk(
    sr_xdf_writer *writer,
    uint32_t chunk_tag,
    uint32_t stream_id,
    int has_stream_id,
    const uint8_t *payload,
    uint64_t payload_size);

SR_XDF_EXPORT int sr_xdf_writer_flush(sr_xdf_writer *writer, int durable);
SR_XDF_EXPORT int sr_xdf_writer_close(sr_xdf_writer *writer, int durable);
/* Close without requiring footers. The partial recovery artifact is retained. */
SR_XDF_EXPORT int sr_xdf_writer_abort(sr_xdf_writer *writer, int durable);
SR_XDF_EXPORT void sr_xdf_writer_destroy(sr_xdf_writer *writer);

/*
 * Strict two-pass merge. Every source must be a complete readable XDF with
 * exactly one file header and closed streams. Sample and clock-offset payload
 * bytes remain unchanged; stream-header XML receives deterministic origin and
 * source-key provenance while the container stream id is remapped. The output
 * is exclusive-create and removed on failure.
 */
SR_XDF_EXPORT int sr_xdf_merge_files(
    const char *const *utf8_source_paths,
    const char *const *source_keys,
    uint64_t source_count,
    const char *utf8_output_path,
    int durable,
    sr_xdf_merge_report *out_report);

#ifdef __cplusplus
}
#endif

#endif
