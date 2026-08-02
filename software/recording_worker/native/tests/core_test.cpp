#include "study_runner_xdf_core.h"

#include <chrono>
#include <cstdint>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

namespace fs = std::filesystem;

namespace {

std::string utf8(const fs::path &path) {
	const auto encoded = path.u8string();
#if defined(__cpp_char8_t)
	return std::string(reinterpret_cast<const char *>(encoded.data()), encoded.size());
#else
	return encoded;
#endif
}

void require(bool condition, const std::string &message) {
	if (!condition) throw std::runtime_error(message);
}

void require_ok(int status, const std::string &operation) {
	if (status != SR_XDF_OK) {
		const std::uint64_t size = sr_xdf_copy_last_error(nullptr, 0);
		std::vector<char> message(static_cast<std::size_t>(size + 1), '\0');
		sr_xdf_copy_last_error(message.data(), message.size());
		throw std::runtime_error(operation + ": " + message.data());
	}
}

std::string stream_header(const std::string &name, const std::string &format,
	std::uint32_t channels, double rate, const std::string &source_id) {
	return "<?xml version=\"1.0\"?><info><name>" + name + "</name><type>Test</type>"
		"<channel_count>" + std::to_string(channels) + "</channel_count><nominal_srate>" +
		std::to_string(rate) + "</nominal_srate><channel_format>" + format +
		"</channel_format><source_id>" + source_id +
		"</source_id><version>1.100000</version><created_at>1</created_at><uid>" + source_id +
		"</uid><session_id>smoke</session_id><hostname>localhost</hostname><desc/></info>";
}

const std::string footer =
	"<?xml version=\"1.0\"?><info><first_timestamp>1</first_timestamp>"
	"<last_timestamp>2</last_timestamp><sample_count>2</sample_count>"
	"<clock_offsets/></info>";

void write_numeric_source(const fs::path &path) {
	sr_xdf_writer *writer = nullptr;
	const std::string path_value = utf8(path);
	require_ok(sr_xdf_writer_open_exclusive(path_value.c_str(), &writer), "open numeric source");
	try {
		const std::string header = stream_header("numeric", "float32", 2, 10.0, "numeric-1");
		require_ok(sr_xdf_writer_write_stream_header(writer, 7,
			reinterpret_cast<const std::uint8_t *>(header.data()), header.size()), "numeric header");
		const double timestamps[] = {1.0, 1.1};
		const float values[] = {1.0F, 2.0F, 3.0F, 4.0F};
		require_ok(sr_xdf_writer_write_numeric_samples(writer, 7, timestamps, 2, values, 4, 2,
			SR_XDF_FLOAT32), "numeric samples");
		require_ok(sr_xdf_writer_write_clock_offset(writer, 7, 1.2, 0.01), "clock offset");
		require_ok(sr_xdf_writer_write_boundary(writer), "boundary");
		require_ok(sr_xdf_writer_write_stream_footer(writer, 7,
			reinterpret_cast<const std::uint8_t *>(footer.data()), footer.size()), "numeric footer");
		require_ok(sr_xdf_writer_close(writer, 1), "close numeric source");
	} catch (...) {
		sr_xdf_writer_destroy(writer);
		throw;
	}
	sr_xdf_writer_destroy(writer);
}

void write_string_source(const fs::path &path) {
	sr_xdf_writer *writer = nullptr;
	const std::string path_value = utf8(path);
	require_ok(sr_xdf_writer_open_exclusive(path_value.c_str(), &writer), "open string source");
	try {
		const std::string header = stream_header("markers", "string", 1, 0.0, "marker-1");
		require_ok(sr_xdf_writer_write_stream_header(writer, 7,
			reinterpret_cast<const std::uint8_t *>(header.data()), header.size()), "string header");
		const double timestamps[] = {1.0, 2.0};
		const std::string packed = "startstop";
		const std::uint64_t offsets[] = {0, 5, 9};
		require_ok(sr_xdf_writer_write_string_samples(writer, 7, timestamps, 2,
			reinterpret_cast<const std::uint8_t *>(packed.data()), packed.size(), offsets, 3, 1),
			"string samples");
		require_ok(sr_xdf_writer_write_stream_footer(writer, 7,
			reinterpret_cast<const std::uint8_t *>(footer.data()), footer.size()), "string footer");
		require_ok(sr_xdf_writer_close(writer, 1), "close string source");
	} catch (...) {
		sr_xdf_writer_destroy(writer);
		throw;
	}
	sr_xdf_writer_destroy(writer);
}

std::uint64_t read_little(std::istream &input, std::uint8_t count) {
	std::uint64_t value = 0;
	for (std::uint8_t index = 0; index < count; ++index) {
		const int byte = input.get();
		if (byte < 0) throw std::runtime_error("truncated fixture");
		value |= static_cast<std::uint64_t>(static_cast<std::uint8_t>(byte)) << (index * 8);
	}
	return value;
}

std::vector<std::string> stream_payloads(const fs::path &path, std::uint16_t wanted_tag) {
	std::ifstream input(path, std::ios::binary);
	char magic[4]{};
	input.read(magic, 4);
	require(std::memcmp(magic, "XDF:", 4) == 0, "bad test XDF magic");
	std::vector<std::string> result;
	while (true) {
		const int size_value = input.get();
		if (size_value < 0) break;
		const auto size = static_cast<std::uint8_t>(size_value);
		const std::uint64_t length = read_little(input, size);
		const std::uint16_t tag = static_cast<std::uint16_t>(read_little(input, 2));
		std::uint64_t payload_size = length - 2;
		const bool stream_specific = tag == 2 || tag == 3 || tag == 4 || tag == 6;
		if (stream_specific) {
			(void)read_little(input, 4);
			payload_size -= 4;
		}
		std::string payload(static_cast<std::size_t>(payload_size), '\0');
		input.read(payload.data(), static_cast<std::streamsize>(payload.size()));
		require(static_cast<std::size_t>(input.gcount()) == payload.size(), "truncated payload");
		if (tag == wanted_tag) result.push_back(std::move(payload));
	}
	return result;
}

} // namespace

int main() {
	const fs::path root = fs::temp_directory_path() /
		("study-runner-xdf-core-" + std::to_string(
			std::chrono::steady_clock::now().time_since_epoch().count()));
	fs::create_directories(root);
	try {
		require(sr_xdf_core_abi_version() == SR_XDF_CORE_ABI_VERSION, "ABI mismatch");
		require(std::strstr(sr_xdf_core_probe_json(), "\"lossless_merge\":true") != nullptr,
			"probe does not advertise merge");
		const fs::path numeric = root / "numeric.xdf";
		const fs::path strings = root / "strings.xdf";
		const fs::path merged = root / "merged.xdf";
		write_numeric_source(numeric);
		write_string_source(strings);
		const fs::path partial = root / "partial.xdf";
		sr_xdf_writer *partial_writer = nullptr;
		require_ok(sr_xdf_writer_open_exclusive(utf8(partial).c_str(), &partial_writer),
			"open partial source");
		const std::string partial_header =
			stream_header("partial", "float32", 1, 1.0, "partial-1");
		require_ok(sr_xdf_writer_write_stream_header(partial_writer, 1,
			reinterpret_cast<const std::uint8_t *>(partial_header.data()), partial_header.size()),
			"partial header");
		require_ok(sr_xdf_writer_abort(partial_writer, 1), "abort partial source");
		sr_xdf_writer_destroy(partial_writer);
		require(fs::is_regular_file(partial) && fs::file_size(partial) > 4,
			"abort did not retain the partial artifact");

		sr_xdf_writer *duplicate = nullptr;
		require(sr_xdf_writer_open_exclusive(utf8(numeric).c_str(), &duplicate) == SR_XDF_IO_ERROR,
			"exclusive create accepted an existing target");
		require(duplicate == nullptr, "exclusive create returned a writer on failure");
		char copied_error[256]{};
		const std::uint64_t full_error_size = sr_xdf_copy_last_error(copied_error, sizeof(copied_error));
		require(full_error_size > 0 && copied_error[0] != '\0', "copy_last_error returned no text");

		const std::string numeric_path = utf8(numeric);
		const std::string strings_path = utf8(strings);
		const char *sources[] = {numeric_path.c_str(), strings_path.c_str()};
		const char *source_keys[] = {"numeric_plugin", "lsl"};
		sr_xdf_merge_report report{};
		require_ok(sr_xdf_merge_files(sources, source_keys, 2, utf8(merged).c_str(), 1,
			&report), "merge");
		require(report.source_count == 2 && report.stream_count == 2, "merge report mismatch");
		require(report.copied_chunk_count >= 7, "merge omitted chunks");

		const auto numeric_samples = stream_payloads(numeric, SR_XDF_SAMPLES);
		const auto string_samples = stream_payloads(strings, SR_XDF_SAMPLES);
		const auto merged_samples = stream_payloads(merged, SR_XDF_SAMPLES);
		require(numeric_samples.size() == 1 && string_samples.size() == 1 &&
			merged_samples.size() == 2, "sample chunk count mismatch");
		require(merged_samples[0] == numeric_samples[0], "numeric payload changed during merge");
		require(merged_samples[1] == string_samples[0], "string payload changed during merge");
		const auto merged_headers = stream_payloads(merged, SR_XDF_STREAM_HEADER);
		require(merged_headers.size() == 2, "merged stream header count mismatch");
		require(merged_headers[0].find(
			"<study_runner_origin_id>numeric_plugin:numeric.xdf:0</study_runner_origin_id>") !=
			std::string::npos, "numeric provenance is missing");
		require(merged_headers[1].find(
			"<study_runner_plugin_key>lsl</study_runner_plugin_key>") != std::string::npos,
			"marker source-key provenance is missing");
		require(stream_payloads(merged, SR_XDF_CLOCK_OFFSET) ==
			stream_payloads(numeric, SR_XDF_CLOCK_OFFSET), "clock payload changed during merge");

		fs::remove_all(root);
		return 0;
	} catch (const std::exception &error) {
		std::cerr << error.what() << '\n';
		fs::remove_all(root);
		return 1;
	}
}
