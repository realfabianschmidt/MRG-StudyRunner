#include "study_runner_xdf_core.h"

#include "xdfwriter_patched.h"

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstring>
#include <exception>
#include <filesystem>
#include <fstream>
#include <limits>
#include <memory>
#include <mutex>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <unordered_set>
#include <utility>
#include <vector>

#ifndef _WIN32
#include <fcntl.h>
#include <unistd.h>
#endif

namespace fs = std::filesystem;

namespace {

constexpr std::uint64_t kMaximumCount =
	static_cast<std::uint64_t>(std::numeric_limits<std::uint32_t>::max());
constexpr std::uint64_t kMaximumChunkPayload = 512ULL * 1024ULL * 1024ULL;
constexpr std::uint64_t kMaximumSourceCount = 4096;
constexpr std::uint64_t kMaximumPathBytes = 1024ULL * 1024ULL;
constexpr std::uint64_t kMaximumXmlBytes = 16ULL * 1024ULL * 1024ULL;

thread_local std::string last_error;

class CoreError final : public std::runtime_error {
public:
	CoreError(int status, const std::string &message) : std::runtime_error(message), status_(status) {}
	int status() const noexcept { return status_; }

private:
	int status_;
};

[[noreturn]] void fail(int status, const std::string &message) {
	throw CoreError(status, message);
}

template <typename Function> int guarded(Function &&function) noexcept {
	try {
		function();
		last_error.clear();
		return SR_XDF_OK;
	} catch (const CoreError &error) {
		last_error = error.what();
		return error.status();
	} catch (const fs::filesystem_error &error) {
		last_error = error.what();
		return SR_XDF_IO_ERROR;
	} catch (const std::ios_base::failure &error) {
		last_error = error.what();
		return SR_XDF_IO_ERROR;
	} catch (const std::invalid_argument &error) {
		last_error = error.what();
		return SR_XDF_INVALID_ARGUMENT;
	} catch (const std::runtime_error &error) {
		last_error = error.what();
		return SR_XDF_IO_ERROR;
	} catch (const std::exception &error) {
		last_error = error.what();
		return SR_XDF_INTERNAL_ERROR;
	} catch (...) {
		last_error = "unknown native XDF core error";
		return SR_XDF_INTERNAL_ERROR;
	}
}

std::string path_text(const fs::path &path) {
	const auto encoded = path.u8string();
#if defined(__cpp_char8_t)
	return std::string(reinterpret_cast<const char *>(encoded.data()), encoded.size());
#else
	return encoded;
#endif
}

std::string require_path(const char *value, const char *field) {
	if (value == nullptr || value[0] == '\0')
		fail(SR_XDF_INVALID_ARGUMENT, std::string(field) + " is required");
	const std::size_t size = std::strlen(value);
	if (size > kMaximumPathBytes)
		fail(SR_XDF_INVALID_ARGUMENT, std::string(field) + " is too long");
	const fs::path path = fs::u8path(value);
	if (!path.is_absolute())
		fail(SR_XDF_INVALID_ARGUMENT, std::string(field) + " must be absolute");
	return std::string(value, size);
}

void sync_parent_directory(const std::string &utf8_path) {
#ifndef _WIN32
	const fs::path parent = fs::u8path(utf8_path).parent_path();
	const int descriptor = ::open(parent.c_str(), O_RDONLY | O_DIRECTORY);
	if (descriptor < 0)
		fail(SR_XDF_IO_ERROR,
			std::string("could not open XDF parent for fsync: ") + std::strerror(errno));
	const int result = ::fsync(descriptor);
	const int error = errno;
	::close(descriptor);
	if (result != 0)
		fail(SR_XDF_IO_ERROR,
			std::string("XDF parent fsync failed: ") + std::strerror(error));
#else
	(void)utf8_path;
#endif
}

std::string bytes_to_string(const std::uint8_t *value, std::uint64_t size, const char *field,
	std::uint64_t maximum = kMaximumChunkPayload) {
	if (size > maximum) fail(SR_XDF_INVALID_ARGUMENT, std::string(field) + " is too large");
	if (size != 0 && value == nullptr)
		fail(SR_XDF_INVALID_ARGUMENT, std::string(field) + " pointer is null");
	return size == 0
		? std::string()
		: std::string(reinterpret_cast<const char *>(value), static_cast<std::size_t>(size));
}

std::uint64_t checked_value_count(std::uint64_t samples, std::uint32_t channels) {
	if (channels == 0) fail(SR_XDF_INVALID_ARGUMENT, "channel_count must be positive");
	if (samples > kMaximumCount)
		fail(SR_XDF_INVALID_ARGUMENT, "sample count exceeds XDFWriter limits");
	if (samples != 0 && channels > std::numeric_limits<std::uint64_t>::max() / samples)
		fail(SR_XDF_INVALID_ARGUMENT, "sample/value count overflow");
	return samples * channels;
}

struct StreamState {
	bool header = false;
	bool footer = false;
};

} // namespace

struct sr_xdf_writer {
	std::mutex mutex;
	std::unique_ptr<XDFWriter> implementation;
	std::string path;
	std::unordered_map<std::uint32_t, StreamState> streams;
	bool closed = false;
};

namespace {

sr_xdf_writer &require_writer(sr_xdf_writer *writer) {
	if (writer == nullptr) fail(SR_XDF_INVALID_ARGUMENT, "writer is null");
	if (writer->closed || !writer->implementation)
		fail(SR_XDF_INVALID_STATE, "writer is closed");
	return *writer;
}

StreamState &require_active_stream(sr_xdf_writer &writer, std::uint32_t stream_id) {
	if (stream_id == 0) fail(SR_XDF_INVALID_ARGUMENT, "stream_id must be positive");
	auto iterator = writer.streams.find(stream_id);
	if (iterator == writer.streams.end() || !iterator->second.header)
		fail(SR_XDF_INVALID_STATE, "stream header has not been written");
	if (iterator->second.footer)
		fail(SR_XDF_INVALID_STATE, "stream footer has already been written");
	return iterator->second;
}

void ensure_all_streams_closed(const sr_xdf_writer &writer) {
	for (const auto &[stream_id, state] : writer.streams) {
		if (state.header && !state.footer)
			fail(SR_XDF_INVALID_STATE,
				"stream " + std::to_string(stream_id) + " has no footer");
	}
}

std::unique_ptr<sr_xdf_writer> make_writer(const std::string &path) {
	const fs::path output_path = fs::u8path(path);
	if (output_path.parent_path().empty() || !fs::is_directory(output_path.parent_path()))
		fail(SR_XDF_IO_ERROR, "XDF output parent directory does not exist");
	auto writer = std::make_unique<sr_xdf_writer>();
	writer->path = path;
	writer->implementation = std::make_unique<XDFWriter>(path);
	return writer;
}

chunk_tag_t checked_tag(std::uint32_t value) {
	if (value < static_cast<std::uint32_t>(chunk_tag_t::fileheader) ||
		value > static_cast<std::uint32_t>(chunk_tag_t::streamfooter))
		fail(SR_XDF_INVALID_ARGUMENT, "unknown XDF chunk tag");
	return static_cast<chunk_tag_t>(value);
}

void append_raw(sr_xdf_writer &writer, chunk_tag_t tag, std::uint32_t stream_id,
	bool has_stream_id, const std::string &payload) {
	if (tag == chunk_tag_t::streamheader) {
		if (!has_stream_id || stream_id == 0)
			fail(SR_XDF_INVALID_ARGUMENT, "stream header requires a positive stream id");
		StreamState &state = writer.streams[stream_id];
		if (state.header) fail(SR_XDF_INVALID_STATE, "duplicate stream header");
		writer.implementation->write_raw_chunk_checked(tag, payload, &stream_id);
		state.header = true;
		return;
	}
	if (tag == chunk_tag_t::boundary) {
		if (has_stream_id) fail(SR_XDF_INVALID_ARGUMENT, "boundary must not have a stream id");
		static const unsigned char expected[] = {0x43, 0xA5, 0x46, 0xDC, 0xCB, 0xF5,
			0x41, 0x0F, 0xB3, 0x0E, 0xD5, 0x46, 0x73, 0x83, 0xCB, 0xE4};
		if (payload.size() != sizeof(expected) ||
			std::memcmp(payload.data(), expected, sizeof(expected)) != 0)
			fail(SR_XDF_INVALID_ARGUMENT, "boundary payload has the wrong signature");
		writer.implementation->write_raw_chunk_checked(tag, payload, nullptr);
		return;
	}
	if (tag == chunk_tag_t::fileheader)
		fail(SR_XDF_INVALID_ARGUMENT, "raw file-header append is not supported");
	if (!has_stream_id)
		fail(SR_XDF_INVALID_ARGUMENT, "stream-specific chunk requires a stream id");
	StreamState &state = require_active_stream(writer, stream_id);
	writer.implementation->write_raw_chunk_checked(tag, payload, &stream_id);
	if (tag == chunk_tag_t::streamfooter) state.footer = true;
}

template <typename Value>
void write_numeric(sr_xdf_writer &writer, std::uint32_t stream_id,
	const double *timestamps, std::uint64_t timestamp_count, const void *values,
	std::uint64_t value_count, std::uint32_t channel_count) {
	const std::uint64_t expected = checked_value_count(timestamp_count, channel_count);
	if (expected != value_count) fail(SR_XDF_INVALID_ARGUMENT, "numeric value count mismatch");
	require_active_stream(writer, stream_id);
	if (timestamp_count == 0) return;
	if (timestamps == nullptr || values == nullptr)
		fail(SR_XDF_INVALID_ARGUMENT, "numeric timestamps and values are required");
	std::vector<double> timestamp_vector(
		timestamps, timestamps + static_cast<std::size_t>(timestamp_count));
	writer.implementation->write_data_chunk<Value>(stream_id, timestamp_vector,
		static_cast<const Value *>(values), static_cast<std::uint32_t>(timestamp_count),
		channel_count);
}

struct ParsedChunk {
	chunk_tag_t tag = chunk_tag_t::undefined;
	std::optional<std::uint32_t> stream_id;
	std::string payload;
};

std::uint64_t read_little(std::istream &input, std::uint8_t byte_count, const fs::path &path) {
	std::uint64_t value = 0;
	for (std::uint8_t index = 0; index < byte_count; ++index) {
		const int byte = input.get();
		if (byte == std::char_traits<char>::eof())
			fail(SR_XDF_CORRUPT_SOURCE,
				"truncated XDF integer in " + path_text(path));
		value |= static_cast<std::uint64_t>(static_cast<std::uint8_t>(byte)) << (8 * index);
	}
	return value;
}

void read_exact(std::istream &input, char *target, std::size_t size, const fs::path &path) {
	if (size == 0) return;
	input.read(target, static_cast<std::streamsize>(size));
	if (input.gcount() != static_cast<std::streamsize>(size))
		fail(SR_XDF_CORRUPT_SOURCE, "truncated XDF chunk in " + path_text(path));
}

template <typename Visitor> void parse_xdf(const std::string &utf8_path, Visitor &&visitor) {
	const fs::path path = fs::u8path(utf8_path);
	if (!fs::is_regular_file(path))
		fail(SR_XDF_IO_ERROR, "XDF source is not a regular file: " + path_text(path));
	std::ifstream input(path, std::ios::binary);
	if (!input) fail(SR_XDF_IO_ERROR, "could not open XDF source: " + path_text(path));
	char magic[4]{};
	read_exact(input, magic, sizeof(magic), path);
	if (std::memcmp(magic, "XDF:", sizeof(magic)) != 0)
		fail(SR_XDF_CORRUPT_SOURCE, "XDF magic mismatch in " + path_text(path));

	std::uint64_t chunk_index = 0;
	while (true) {
		const int length_size_value = input.get();
		if (length_size_value == std::char_traits<char>::eof()) {
			if (input.eof()) break;
			fail(SR_XDF_IO_ERROR, "could not read XDF source: " + path_text(path));
		}
		const std::uint8_t length_size = static_cast<std::uint8_t>(length_size_value);
		if (length_size != 1 && length_size != 4 && length_size != 8)
			fail(SR_XDF_CORRUPT_SOURCE,
				"invalid XDF variable-length integer in " + path_text(path));
		const std::uint64_t chunk_length = read_little(input, length_size, path);
		if (chunk_length < sizeof(std::uint16_t))
			fail(SR_XDF_CORRUPT_SOURCE, "invalid XDF chunk length in " + path_text(path));
		const std::uint64_t tag_value = read_little(input, sizeof(std::uint16_t), path);
		if (tag_value < 1 || tag_value > 6)
			fail(SR_XDF_CORRUPT_SOURCE, "unknown XDF chunk tag in " + path_text(path));
		ParsedChunk chunk;
		chunk.tag = static_cast<chunk_tag_t>(tag_value);
		std::uint64_t payload_size = chunk_length - sizeof(std::uint16_t);
		const bool stream_specific = chunk.tag == chunk_tag_t::streamheader ||
			chunk.tag == chunk_tag_t::samples || chunk.tag == chunk_tag_t::clockoffset ||
			chunk.tag == chunk_tag_t::streamfooter;
		if (stream_specific) {
			if (payload_size < sizeof(std::uint32_t))
				fail(SR_XDF_CORRUPT_SOURCE,
					"stream-specific XDF chunk has no stream id in " + path_text(path));
			chunk.stream_id = static_cast<std::uint32_t>(
				read_little(input, sizeof(std::uint32_t), path));
			if (*chunk.stream_id == 0)
				fail(SR_XDF_CORRUPT_SOURCE, "XDF stream id is zero in " + path_text(path));
			payload_size -= sizeof(std::uint32_t);
		}
		if (payload_size > kMaximumChunkPayload ||
			payload_size > static_cast<std::uint64_t>(std::numeric_limits<std::size_t>::max()))
			fail(SR_XDF_CORRUPT_SOURCE, "XDF chunk exceeds the audited size limit");
		chunk.payload.resize(static_cast<std::size_t>(payload_size));
		read_exact(input, chunk.payload.data(), chunk.payload.size(), path);
		visitor(chunk, chunk_index++);
	}
	if (!input.eof()) fail(SR_XDF_IO_ERROR, "XDF source read failed: " + path_text(path));
}

struct SourceAnalysis {
	std::unordered_map<std::uint32_t, StreamState> streams;
	std::vector<std::uint32_t> stream_order;
	std::uint64_t file_headers = 0;
	std::uint64_t chunk_count = 0;
	std::uintmax_t file_size = 0;
	fs::file_time_type last_write_time{};
};

SourceAnalysis analyze_source(const std::string &path) {
	SourceAnalysis analysis;
	const fs::path source_path = fs::u8path(path);
	analysis.file_size = fs::file_size(source_path);
	analysis.last_write_time = fs::last_write_time(source_path);
	parse_xdf(path, [&](const ParsedChunk &chunk, std::uint64_t index) {
		++analysis.chunk_count;
		if (index == 0 && chunk.tag != chunk_tag_t::fileheader)
			fail(SR_XDF_CORRUPT_SOURCE, "first XDF chunk is not a file header");
		if (chunk.tag == chunk_tag_t::fileheader) {
			++analysis.file_headers;
			if (index != 0 || analysis.file_headers != 1 || chunk.payload.empty())
				fail(SR_XDF_CORRUPT_SOURCE, "invalid or duplicate XDF file header");
			return;
		}
		if (analysis.file_headers != 1)
			fail(SR_XDF_CORRUPT_SOURCE, "XDF content appears before its file header");
		if (chunk.tag == chunk_tag_t::boundary) {
			static const unsigned char expected[] = {0x43, 0xA5, 0x46, 0xDC, 0xCB, 0xF5,
				0x41, 0x0F, 0xB3, 0x0E, 0xD5, 0x46, 0x73, 0x83, 0xCB, 0xE4};
			if (chunk.payload.size() != sizeof(expected) ||
				std::memcmp(chunk.payload.data(), expected, sizeof(expected)) != 0)
				fail(SR_XDF_CORRUPT_SOURCE, "invalid XDF boundary signature");
			return;
		}
		const std::uint32_t stream_id = *chunk.stream_id;
		StreamState &state = analysis.streams[stream_id];
		if (chunk.tag == chunk_tag_t::streamheader) {
			if (state.header || chunk.payload.empty())
				fail(SR_XDF_CORRUPT_SOURCE, "duplicate or empty XDF stream header");
			state.header = true;
			analysis.stream_order.push_back(stream_id);
			return;
		}
		if (!state.header || state.footer)
			fail(SR_XDF_CORRUPT_SOURCE, "XDF stream chunk is outside an open stream");
		if (chunk.tag == chunk_tag_t::clockoffset && chunk.payload.size() != 16)
			fail(SR_XDF_CORRUPT_SOURCE, "XDF clock-offset payload is not 16 bytes");
		if (chunk.tag == chunk_tag_t::samples && chunk.payload.empty())
			fail(SR_XDF_CORRUPT_SOURCE, "XDF samples payload is empty");
		if (chunk.tag == chunk_tag_t::streamfooter) {
			if (chunk.payload.empty())
				fail(SR_XDF_CORRUPT_SOURCE, "XDF stream footer is empty");
			state.footer = true;
		}
	});
	if (analysis.file_headers != 1)
		fail(SR_XDF_CORRUPT_SOURCE, "XDF source has no file header");
	if (analysis.streams.empty())
		fail(SR_XDF_CORRUPT_SOURCE, "XDF source has no streams");
	for (const auto &[stream_id, state] : analysis.streams) {
		if (!state.header || !state.footer)
			fail(SR_XDF_CORRUPT_SOURCE,
				"XDF stream " + std::to_string(stream_id) + " is not closed");
	}
	return analysis;
}

void require_unchanged_source(const std::string &path, const SourceAnalysis &analysis) {
	const fs::path source_path = fs::u8path(path);
	if (fs::file_size(source_path) != analysis.file_size ||
		fs::last_write_time(source_path) != analysis.last_write_time)
		fail(SR_XDF_CORRUPT_SOURCE, "XDF source changed between merge passes");
}

std::string require_source_key(const char *value) {
	if (value == nullptr || value[0] == '\0')
		fail(SR_XDF_INVALID_ARGUMENT, "source key is required");
	const std::size_t size = std::strlen(value);
	if (size > 128) fail(SR_XDF_INVALID_ARGUMENT, "source key is too long");
	for (const unsigned char character : std::string(value, size)) {
		if (!((character >= 'a' && character <= 'z') ||
			  (character >= 'A' && character <= 'Z') ||
			  (character >= '0' && character <= '9') || character == '_' ||
			  character == '-' || character == '.'))
			fail(SR_XDF_INVALID_ARGUMENT, "source key contains unsupported characters");
	}
	return std::string(value, size);
}

std::string xml_escape(const std::string &value) {
	std::string escaped;
	escaped.reserve(value.size());
	for (const char character : value) {
		switch (character) {
		case '&': escaped += "&amp;"; break;
		case '<': escaped += "&lt;"; break;
		case '>': escaped += "&gt;"; break;
		case '\"': escaped += "&quot;"; break;
		case '\'': escaped += "&apos;"; break;
		default: escaped.push_back(character); break;
		}
	}
	return escaped;
}

std::string header_with_provenance(const std::string &header, const std::string &source_key,
	const std::string &source_path, std::size_t stream_index) {
	if (header.find("study_runner_origin_id") != std::string::npos ||
		header.find("study_runner_plugin_key") != std::string::npos)
		fail(SR_XDF_CORRUPT_SOURCE, "raw XDF stream header already contains merge provenance");
	const std::size_t closing_info = header.rfind("</info>");
	if (closing_info == std::string::npos)
		fail(SR_XDF_CORRUPT_SOURCE, "XDF stream header has no closing info element");
	const std::string source_filename = path_text(fs::u8path(source_path).filename());
	const std::string origin = source_key + ":" + source_filename + ":" +
		std::to_string(stream_index);
	const std::string metadata =
		"<study_runner_origin_id>" + xml_escape(origin) + "</study_runner_origin_id>"
		"<study_runner_plugin_key>" + xml_escape(source_key) + "</study_runner_plugin_key>";
	std::string result = header;
	result.insert(closing_info, metadata);
	return result;
}

} // namespace

extern "C" {

uint32_t sr_xdf_core_abi_version(void) { return SR_XDF_CORE_ABI_VERSION; }

const char *sr_xdf_core_probe_json(void) {
	static const std::string probe = [] {
		const std::uint16_t marker = 1;
		const bool little_endian = *reinterpret_cast<const std::uint8_t *>(&marker) == 1;
		std::ostringstream json;
		json << "{\"abi_version\":1,\"implementation\":\"App-LabRecorder/XDFWriter\",";
		json << "\"upstream_version\":\"v1.17.1\",\"canonical_xdf\":"
			 << (little_endian ? "true" : "false") << ',';
		json << "\"features\":{\"typed_batches\":true,\"string_batches\":true,";
		json << "\"clock_offsets\":true,\"boundaries\":true,\"exclusive_create\":true,";
		json << "\"durable_flush\":true,\"checked_raw_chunks\":true,";
		json << "\"lossless_merge\":true},\"byte_order\":\""
			 << (little_endian ? "little" : "unsupported") << "\"}";
		return json.str();
	}();
	return probe.c_str();
}

std::uint64_t sr_xdf_copy_last_error(char *buffer, std::uint64_t capacity) {
	const std::uint64_t required = static_cast<std::uint64_t>(last_error.size());
	if (buffer != nullptr && capacity > 0) {
		const std::uint64_t copied = std::min(required, capacity - 1);
		if (copied > 0) std::memcpy(buffer, last_error.data(), static_cast<std::size_t>(copied));
		buffer[copied] = '\0';
	}
	return required;
}

int sr_xdf_writer_open_exclusive(const char *utf8_path, sr_xdf_writer **out_writer) {
	return guarded([&] {
		if (out_writer == nullptr) fail(SR_XDF_INVALID_ARGUMENT, "out_writer is null");
		*out_writer = nullptr;
		const std::string path = require_path(utf8_path, "utf8_path");
		*out_writer = make_writer(path).release();
	});
}

int sr_xdf_writer_write_stream_header(sr_xdf_writer *writer, std::uint32_t stream_id,
	const std::uint8_t *xml, std::uint64_t xml_size) {
	return guarded([&] {
		if (stream_id == 0) fail(SR_XDF_INVALID_ARGUMENT, "stream_id must be positive");
		const std::string content = bytes_to_string(xml, xml_size, "stream header", kMaximumXmlBytes);
		if (content.empty()) fail(SR_XDF_INVALID_ARGUMENT, "stream header must not be empty");
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		StreamState &state = target.streams[stream_id];
		if (state.header) fail(SR_XDF_INVALID_STATE, "duplicate stream header");
		target.implementation->write_stream_header(stream_id, content);
		state.header = true;
	});
}

int sr_xdf_writer_write_stream_footer(sr_xdf_writer *writer, std::uint32_t stream_id,
	const std::uint8_t *xml, std::uint64_t xml_size) {
	return guarded([&] {
		const std::string content = bytes_to_string(xml, xml_size, "stream footer", kMaximumXmlBytes);
		if (content.empty()) fail(SR_XDF_INVALID_ARGUMENT, "stream footer must not be empty");
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		StreamState &state = require_active_stream(target, stream_id);
		target.implementation->write_stream_footer(stream_id, content);
		state.footer = true;
	});
}

int sr_xdf_writer_write_numeric_samples(sr_xdf_writer *writer, std::uint32_t stream_id,
	const double *timestamps, std::uint64_t timestamp_count, const void *values,
	std::uint64_t value_count, std::uint32_t channel_count, std::uint32_t value_format) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		switch (value_format) {
		case SR_XDF_INT8:
			write_numeric<std::int8_t>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		case SR_XDF_INT16:
			write_numeric<std::int16_t>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		case SR_XDF_INT32:
			write_numeric<std::int32_t>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		case SR_XDF_INT64:
			write_numeric<std::int64_t>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		case SR_XDF_FLOAT32:
			write_numeric<float>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		case SR_XDF_FLOAT64:
			write_numeric<double>(target, stream_id, timestamps, timestamp_count, values,
				value_count, channel_count);
			break;
		default: fail(SR_XDF_INVALID_ARGUMENT, "unknown numeric value format");
		}
	});
}

int sr_xdf_writer_write_string_samples(sr_xdf_writer *writer, std::uint32_t stream_id,
	const double *timestamps, std::uint64_t timestamp_count, const std::uint8_t *packed_utf8,
	std::uint64_t packed_utf8_size, const std::uint64_t *offsets,
	std::uint64_t offset_count, std::uint32_t channel_count) {
	return guarded([&] {
		const std::uint64_t value_count = checked_value_count(timestamp_count, channel_count);
		if (value_count == std::numeric_limits<std::uint64_t>::max() ||
			offset_count != value_count + 1)
			fail(SR_XDF_INVALID_ARGUMENT, "string offset count mismatch");
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		require_active_stream(target, stream_id);
		if (timestamp_count == 0) {
			if (offset_count != 1 || offsets == nullptr || offsets[0] != 0 || packed_utf8_size != 0)
				fail(SR_XDF_INVALID_ARGUMENT, "empty string batch has invalid offsets");
			return;
		}
		if (timestamps == nullptr || offsets == nullptr ||
			(packed_utf8_size != 0 && packed_utf8 == nullptr))
			fail(SR_XDF_INVALID_ARGUMENT, "string batch pointers are incomplete");
		if (packed_utf8_size > kMaximumChunkPayload)
			fail(SR_XDF_INVALID_ARGUMENT, "packed string payload is too large");
		if (offsets[0] != 0 || offsets[offset_count - 1] != packed_utf8_size)
			fail(SR_XDF_INVALID_ARGUMENT, "string offsets do not span the packed payload");
		const char *packed = packed_utf8_size == 0
			? ""
			: reinterpret_cast<const char *>(packed_utf8);
		std::vector<std::string> values;
		values.reserve(static_cast<std::size_t>(value_count));
		for (std::uint64_t index = 0; index < value_count; ++index) {
			if (offsets[index] > offsets[index + 1] || offsets[index + 1] > packed_utf8_size)
				fail(SR_XDF_INVALID_ARGUMENT, "string offsets are not monotonic");
			const std::uint64_t begin = offsets[index];
			const std::uint64_t length = offsets[index + 1] - begin;
			values.emplace_back(packed + begin,
				static_cast<std::size_t>(length));
		}
		std::vector<double> timestamp_vector(
			timestamps, timestamps + static_cast<std::size_t>(timestamp_count));
		target.implementation->write_data_chunk<std::string>(stream_id, timestamp_vector,
			values.data(), static_cast<std::uint32_t>(timestamp_count), channel_count);
	});
}

int sr_xdf_writer_write_clock_offset(sr_xdf_writer *writer, std::uint32_t stream_id,
	double collection_time, double offset) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		require_active_stream(target, stream_id);
		target.implementation->write_stream_offset(stream_id, collection_time, offset);
	});
}

int sr_xdf_writer_write_boundary(sr_xdf_writer *writer) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		target.implementation->write_boundary_chunk();
	});
}

int sr_xdf_writer_write_raw_chunk(sr_xdf_writer *writer, std::uint32_t chunk_tag,
	std::uint32_t stream_id, int has_stream_id, const std::uint8_t *payload,
	std::uint64_t payload_size) {
	return guarded([&] {
		const std::string content = bytes_to_string(payload, payload_size, "raw chunk payload");
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		append_raw(target, checked_tag(chunk_tag), stream_id, has_stream_id != 0, content);
	});
}

int sr_xdf_writer_flush(sr_xdf_writer *writer, int durable) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		target.implementation->flush(durable != 0);
		if (durable != 0) sync_parent_directory(target.path);
	});
}

int sr_xdf_writer_close(sr_xdf_writer *writer, int durable) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		ensure_all_streams_closed(target);
		target.implementation->flush(durable != 0);
		target.implementation.reset();
		target.closed = true;
		if (durable != 0) sync_parent_directory(target.path);
	});
}

int sr_xdf_writer_abort(sr_xdf_writer *writer, int durable) {
	return guarded([&] {
		sr_xdf_writer &target = require_writer(writer);
		std::lock_guard<std::mutex> lock(target.mutex);
		std::exception_ptr flush_error;
		try {
			target.implementation->flush(durable != 0);
		} catch (...) {
			flush_error = std::current_exception();
		}
		target.implementation.reset();
		target.closed = true;
		if (durable != 0) sync_parent_directory(target.path);
		if (flush_error) std::rethrow_exception(flush_error);
	});
}

void sr_xdf_writer_destroy(sr_xdf_writer *writer) {
	try {
		delete writer;
	} catch (...) {
	}
}

int sr_xdf_merge_files(const char *const *utf8_source_paths, const char *const *source_keys,
	std::uint64_t source_count, const char *utf8_output_path, int durable,
	sr_xdf_merge_report *out_report) {
	return guarded([&] {
		if (utf8_source_paths == nullptr || source_keys == nullptr || source_count == 0 ||
			source_count > kMaximumSourceCount)
			fail(SR_XDF_INVALID_ARGUMENT, "source path list is empty or too large");
		if (out_report == nullptr) fail(SR_XDF_INVALID_ARGUMENT, "out_report is null");
		*out_report = {};
		const std::string output_path = require_path(utf8_output_path, "utf8_output_path");
		std::vector<std::string> source_paths;
		std::vector<std::string> validated_source_keys;
		source_paths.reserve(static_cast<std::size_t>(source_count));
		validated_source_keys.reserve(static_cast<std::size_t>(source_count));
		std::unordered_set<std::string> unique_paths;
		for (std::uint64_t index = 0; index < source_count; ++index) {
			const std::string path = require_path(utf8_source_paths[index], "source path");
			const std::string normalized = path_text(fs::weakly_canonical(fs::u8path(path)));
			if (!unique_paths.insert(normalized).second)
				fail(SR_XDF_INVALID_ARGUMENT, "duplicate XDF source path");
			if (fs::u8path(path).lexically_normal() == fs::u8path(output_path).lexically_normal())
				fail(SR_XDF_INVALID_ARGUMENT, "merge output must differ from every source");
			source_paths.push_back(path);
			validated_source_keys.push_back(require_source_key(source_keys[index]));
		}

		std::vector<SourceAnalysis> analyses;
		std::vector<std::unordered_map<std::uint32_t, std::uint32_t>> remaps;
		analyses.reserve(source_paths.size());
		remaps.reserve(source_paths.size());
		std::uint64_t next_stream_id = 1;
		for (const std::string &path : source_paths) {
			SourceAnalysis analysis = analyze_source(path);
			std::unordered_map<std::uint32_t, std::uint32_t> remap;
			for (const std::uint32_t old_id : analysis.stream_order) {
				if (next_stream_id > kMaximumCount)
					fail(SR_XDF_UNSUPPORTED, "merged XDF has too many streams");
				remap.emplace(old_id, static_cast<std::uint32_t>(next_stream_id++));
			}
			analyses.push_back(std::move(analysis));
			remaps.push_back(std::move(remap));
		}

		std::unique_ptr<sr_xdf_writer> output;
		bool output_created = false;
		try {
			output = make_writer(output_path);
			output_created = true;
			std::uint64_t copied_chunks = 0;
			std::uint64_t copied_payload_bytes = 0;
			for (std::size_t source_index = 0; source_index < source_paths.size(); ++source_index) {
				require_unchanged_source(source_paths[source_index], analyses[source_index]);
				parse_xdf(source_paths[source_index], [&](const ParsedChunk &chunk, std::uint64_t) {
					if (chunk.tag == chunk_tag_t::fileheader) return;
					std::uint32_t mapped_id = 0;
					if (chunk.stream_id) {
						auto iterator = remaps[source_index].find(*chunk.stream_id);
						if (iterator == remaps[source_index].end())
							fail(SR_XDF_CORRUPT_SOURCE, "stream id changed between merge passes");
						mapped_id = iterator->second;
					}
					std::string payload = chunk.payload;
					if (chunk.tag == chunk_tag_t::streamheader) {
						const auto &order = analyses[source_index].stream_order;
						const auto position = std::find(order.begin(), order.end(), *chunk.stream_id);
						if (position == order.end())
							fail(SR_XDF_CORRUPT_SOURCE, "stream order changed between merge passes");
						payload = header_with_provenance(chunk.payload,
							validated_source_keys[source_index], source_paths[source_index],
							static_cast<std::size_t>(position - order.begin()));
					}
					append_raw(*output, chunk.tag, mapped_id, chunk.stream_id.has_value(), payload);
					++copied_chunks;
					copied_payload_bytes += payload.size();
				});
				require_unchanged_source(source_paths[source_index], analyses[source_index]);
			}
			ensure_all_streams_closed(*output);
			output->implementation->flush(durable != 0);
			output->implementation.reset();
			output->closed = true;
			if (durable != 0) sync_parent_directory(output_path);
			out_report->abi_version = SR_XDF_CORE_ABI_VERSION;
			out_report->source_count = static_cast<std::uint32_t>(source_count);
			out_report->stream_count = static_cast<std::uint32_t>(next_stream_id - 1);
			out_report->copied_chunk_count = copied_chunks;
			out_report->copied_payload_bytes = copied_payload_bytes;
		} catch (...) {
			output.reset();
			std::error_code ignored;
			if (output_created) fs::remove(fs::u8path(output_path), ignored);
			throw;
		}
	});
}

} // extern "C"
