// Derived from App-LabRecorder's XDFWriter by Christian Kothe and modified for
// Study Runner's small C ABI and durable flush support. This file remains under
// the upstream MIT License in vendor/App-LabRecorder/LICENSE.
#pragma once

#include "conversions.h"

#include <cassert>
#include <memory>
#include <mutex>
#include <ostream>
#include <sstream>
#include <string>
#include <thread>
#include <type_traits>
#include <vector>

using streamid_t = uint32_t;

class XDFWriterOutput;

// the currently defined chunk tags
enum class chunk_tag_t : uint16_t {
	fileheader = 1,   // FileHeader chunk
	streamheader = 2, // StreamHeader chunk
	samples = 3,	  // Samples chunk
	clockoffset = 4,  // ClockOffset chunk
	boundary = 5,	 // Boundary chunk
	streamfooter = 6, // StreamFooter chunk
	undefined = 0
};

class XDFWriter {
private:
	std::unique_ptr<XDFWriterOutput> output_;
	std::ostream &file_;
	std::string filename_;
	void _write_chunk_header(
		chunk_tag_t tag, std::size_t length, const streamid_t *streamid_p = nullptr);
	std::mutex write_mut;

	// write a generic chunk
	void _write_chunk(
		chunk_tag_t tag, const std::string &content, const streamid_t *streamid_p = nullptr);

public:
	/**
	 * @brief XDFWriter Construct a XDFWriter object
	 * @param filename  Filename to write to
	 */
	XDFWriter(const std::string &filename);
	~XDFWriter();

	template <typename T>
	void write_data_chunk(streamid_t streamid, const std::vector<double> &timestamps,
		const T *chunk, uint32_t n_samples, uint32_t n_channels);
	template <typename T>
	void write_data_chunk(streamid_t streamid, const std::vector<double> &timestamps,
		const std::vector<T> &chunk, uint32_t n_channels) {
		assert(timestamps.size() * n_channels == chunk.size());
		write_data_chunk(streamid, timestamps, chunk.data(), (uint32_t)timestamps.size(), n_channels);
	}
	template <typename T>
	void write_data_chunk_nested(streamid_t streamid, const std::vector<double> &timestamps,
		const std::vector<std::vector<T>> &chunk);

	/**
	 * @brief write_stream_header Write the stream header, see also
	 * @see https://github.com/sccn/xdf/wiki/Specifications#clockoffset-chunk
	 * @param streamid Numeric stream identifier
	 * @param content XML-formatted stream header
	 */
	void write_stream_header(streamid_t streamid, const std::string &content);
	/**
	 * @brief write_stream_footer
	 * @see https://github.com/sccn/xdf/wiki/Specifications#streamfooter-chunk
	 */
	void write_stream_footer(streamid_t streamid, const std::string &content);
	/**
	 * @brief write_stream_offset Record the time discrepancy between the
	 * streaming and the recording PC
	 * @see https://github.com/sccn/xdf/wiki/Specifications#clockoffset-chunk
	 */
	void write_stream_offset(streamid_t streamid, double collectiontime, double offset);
	/**
	 * @brief write_boundary_chunk Insert a boundary chunk that's mostly used
	 * to recover from errors in XDF files by providing a restart marker.
	 */
	void write_boundary_chunk();

	/**
	 * Study Runner audit patch: append an already encoded XDF payload while
	 * retaining XDFWriter's canonical chunk framing.  Only standard chunk tags
	 * are accepted and stream-id presence is checked before any bytes are
	 * written.  This is used by the lossless merger to preserve sample and
	 * clock-offset payloads byte-for-byte.
	 */
	void write_raw_chunk_checked(
		chunk_tag_t tag, const std::string &content, const streamid_t *streamid_p = nullptr);

	/**
	 * Study Runner audit patch: flush C++ buffers and optionally request an OS
	 * durable sync for the same path.  The wrapper performs exclusive creation
	 * before constructing XDFWriter.
	 */
	void flush(bool durable);
};

inline void write_ts(std::ostream &out, double ts) {
	// write timestamp
	if (ts == 0)
		out.put(0);
	else {
		// [TimeStampBytes]
		out.put(8);
		// [TimeStamp]
		write_little_endian(out, ts);
	}
}

template <typename T>
void XDFWriter::write_data_chunk(streamid_t streamid, const std::vector<double> &timestamps,
	const T *chunk, uint32_t n_samples, uint32_t n_channels) {
	/**
	  Samples data chunk: [Tag 3] [VLA ChunkLen] [StreamID] [VLA NumSamples]
	  [NumSamples x [VLA TimestampLen] [TimeStampLen]
	  [NumSamples x NumChannels Sample]
	  */
	if (n_samples == 0) return;
	if (timestamps.size() != n_samples)
		throw std::runtime_error("timestamp / sample count mismatch");

	// generate [Samples] chunk contents...

	std::ostringstream out;
	write_fixlen_int(out, 0x0FFFFFFF); // Placeholder length, will be replaced later
	for (double ts : timestamps) {
		write_ts(out, ts);
		// write sample, get the current position in the chunk array back
		chunk = write_sample_values(out, chunk, n_channels);
	}
	std::string outstr(out.str());
	// Replace length placeholder
	auto s = static_cast<uint32_t>(n_samples);
	std::copy(reinterpret_cast<char *>(&s), reinterpret_cast<char *>(&s + 1), outstr.begin() + 1);

	std::lock_guard<std::mutex> lock(write_mut);
	_write_chunk(chunk_tag_t::samples, outstr, &streamid);
}

template <typename T>
void XDFWriter::write_data_chunk_nested(streamid_t streamid, const std::vector<double> &timestamps,
	const std::vector<std::vector<T>> &chunk) {
	if (chunk.size() == 0) return;
	auto n_samples = timestamps.size();
	if (timestamps.size() != chunk.size())
		throw std::runtime_error("timestamp / sample count mismatch");
	auto n_channels = chunk[0].size();

	// generate [Samples] chunk contents...

	std::ostringstream out;
	write_fixlen_int(out, 0x0FFFFFFF); // Placeholder length, will be replaced later
	auto sample_it = chunk.cbegin();
	for (double ts : timestamps) {
		assert(n_channels == sample_it->size());
		write_ts(out, ts);
		// write sample, get the current position in the chunk array back
		write_sample_values(out, sample_it->data(), n_channels);
		sample_it++;
	}
	std::string outstr(out.str());
	// Replace length placeholder
	auto s = static_cast<uint32_t>(n_samples);
	std::copy(reinterpret_cast<char *>(&s), reinterpret_cast<char *>(&s + 1), outstr.begin() + 1);
	std::lock_guard<std::mutex> lock(write_mut);
	_write_chunk(chunk_tag_t::samples, outstr, &streamid);
}
