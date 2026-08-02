// Derived from App-LabRecorder's XDFWriter by Christian Kothe and modified for
// Study Runner's small C ABI and durable flush support. This file remains under
// the upstream MIT License in vendor/App-LabRecorder/LICENSE.
#define _CRT_SECURE_NO_WARNINGS
#include "xdfwriter_patched.h"
#include <algorithm>
#include <iostream>
#include <iomanip>
#include <chrono>
#include <ctime>
#include <cerrno>
#include <cstring>
#include <filesystem>
#include <memory>
#include <limits>
#include <streambuf>
#include <stdexcept>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <fcntl.h>
#include <unistd.h>
#endif

class NativeExclusiveFileBuffer final : public std::streambuf {
public:
	explicit NativeExclusiveFileBuffer(const std::string &filename) {
#ifdef _WIN32
		const int required = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename.c_str(),
			-1, nullptr, 0);
		if (required <= 0) throw std::runtime_error("XDF path is not valid UTF-8");
		std::wstring wide(static_cast<std::size_t>(required), L'\0');
		if (MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, filename.c_str(), -1,
			wide.data(), required) <= 0)
			throw std::runtime_error("XDF path conversion failed");
		handle_ = CreateFileW(wide.c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW,
			FILE_ATTRIBUTE_NORMAL, nullptr);
		if (handle_ == INVALID_HANDLE_VALUE)
			throw std::runtime_error(
				"exclusive XDF create failed with error " + std::to_string(GetLastError()));
#else
		descriptor_ = ::open(filename.c_str(), O_WRONLY | O_CREAT | O_EXCL, 0600);
		if (descriptor_ < 0)
			throw std::runtime_error(std::string("exclusive XDF create failed: ") +
				std::strerror(errno));
#endif
	}

	~NativeExclusiveFileBuffer() override {
#ifdef _WIN32
		if (handle_ != INVALID_HANDLE_VALUE) CloseHandle(handle_);
#else
		if (descriptor_ >= 0) ::close(descriptor_);
#endif
	}

	void durable_sync() {
#ifdef _WIN32
		if (!FlushFileBuffers(handle_))
			throw std::runtime_error(
				"FlushFileBuffers failed with error " + std::to_string(GetLastError()));
#else
		if (::fsync(descriptor_) != 0)
			throw std::runtime_error(std::string("fsync failed: ") + std::strerror(errno));
#endif
	}

protected:
	std::streamsize xsputn(const char *source, std::streamsize count) override {
		std::streamsize written = 0;
		while (written < count) {
#ifdef _WIN32
			const std::streamsize remaining = count - written;
			const DWORD requested = static_cast<DWORD>(std::min<std::streamsize>(
				remaining, static_cast<std::streamsize>(std::numeric_limits<DWORD>::max())));
			DWORD current = 0;
			if (!WriteFile(handle_, source + written, requested, &current, nullptr) || current == 0)
				break;
			written += current;
#else
			const ssize_t current = ::write(descriptor_, source + written,
				static_cast<std::size_t>(count - written));
			if (current > 0) {
				written += current;
				continue;
			}
			if (current < 0 && errno == EINTR) continue;
			break;
#endif
		}
		return written;
	}

	int_type overflow(int_type value) override {
		if (traits_type::eq_int_type(value, traits_type::eof())) return traits_type::not_eof(value);
		const char byte = traits_type::to_char_type(value);
		return xsputn(&byte, 1) == 1 ? value : traits_type::eof();
	}

	int sync() override { return 0; }

private:
#ifdef _WIN32
	HANDLE handle_ = INVALID_HANDLE_VALUE;
#else
	int descriptor_ = -1;
#endif
};

class XDFWriterOutput final {
public:
	explicit XDFWriterOutput(const std::string &filename) : buffer_(filename), stream_(&buffer_) {}
	std::ostream &stream() { return stream_; }
	void durable_sync() { buffer_.durable_sync(); }

private:
	NativeExclusiveFileBuffer buffer_;
	std::ostream stream_;
};

void write_timestamp(std::ostream &out, double ts) {
	// [TimeStampBytes] (0 for no time stamp)
	if (ts == 0)
		out.put(0);
	else {
		// [TimeStampBytes]
		out.put(8);
		// [TimeStamp]
		write_little_endian(out, ts);
	}
}

XDFWriter::XDFWriter(const std::string &filename)
	: output_(std::make_unique<XDFWriterOutput>(filename)), file_(output_->stream()),
	  filename_(filename) {
	if (!file_) throw std::runtime_error("could not open XDF output");
	// [MagicCode]
	file_ << "XDF:";
	// [FileHeader] chunk
	std::stringstream header;
	header << "<?xml version=\"1.0\"?>\n  <info>\n    <version>1.0</version>";
	// datetime
	std::time_t now = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
	header << "\n    <datetime>" << std::put_time(std::localtime(&now), "%FT%T%z") << "</datetime>";
	header << "\n  </info>";
	_write_chunk(chunk_tag_t::fileheader, header.str());
	if (!file_) throw std::runtime_error("could not write XDF file header");
}

XDFWriter::~XDFWriter() = default;

void XDFWriter::_write_chunk(
	chunk_tag_t tag, const std::string &content, const streamid_t *streamid_p) {
	// Write the chunk header
	_write_chunk_header(tag, content.length(), streamid_p);
	// [Content]
	file_ << content;
}

void XDFWriter::_write_chunk_header(
	chunk_tag_t tag, std::size_t len, const streamid_t *streamid_p) {
	len += sizeof(chunk_tag_t);
	if (streamid_p) len += sizeof(streamid_t);

	// [Length] (variable-length integer, content + 2 bytes for the tag
	// + 4 bytes if the streamid is being written
	write_varlen_int(file_, len);
	// [Tag]
	write_little_endian(file_, static_cast<uint16_t>(tag));
	// Optional: [StreamId]
	if (streamid_p) write_little_endian(file_, *streamid_p);
}

void XDFWriter::write_stream_header(streamid_t streamid, const std::string &content) {
	std::lock_guard<std::mutex> lock(write_mut);
	_write_chunk(chunk_tag_t::streamheader, content, &streamid);
}

void XDFWriter::write_stream_footer(streamid_t streamid, const std::string &content) {
	std::lock_guard<std::mutex> lock(write_mut);
	_write_chunk(chunk_tag_t::streamfooter, content, &streamid);
}

void XDFWriter::write_stream_offset(streamid_t streamid, double now, double offset) {
	std::lock_guard<std::mutex> lock(write_mut);
	const auto len = sizeof(now) + sizeof(offset);
	_write_chunk_header(chunk_tag_t::clockoffset, len, &streamid);
	// [CollectionTime]
	write_little_endian(file_, now - offset);
	// [OffsetValue]
	write_little_endian(file_, offset);
}

void XDFWriter::write_boundary_chunk() {
	std::lock_guard<std::mutex> lock(write_mut);
	// the signature of the boundary chunk (next chunk begins right after this)
	const uint8_t boundary_uuid[] = {0x43, 0xA5, 0x46, 0xDC, 0xCB, 0xF5, 0x41, 0x0F, 0xB3, 0x0E,
		0xD5, 0x46, 0x73, 0x83, 0xCB, 0xE4};
	_write_chunk_header(chunk_tag_t::boundary, sizeof(boundary_uuid));
	write_sample_values(file_, boundary_uuid, sizeof(boundary_uuid));
}

void XDFWriter::write_raw_chunk_checked(
	chunk_tag_t tag, const std::string &content, const streamid_t *streamid_p) {
	const bool stream_specific = tag == chunk_tag_t::streamheader ||
		tag == chunk_tag_t::samples || tag == chunk_tag_t::clockoffset ||
		tag == chunk_tag_t::streamfooter;
	if (tag == chunk_tag_t::undefined || tag == chunk_tag_t::fileheader)
		throw std::invalid_argument("raw append rejects undefined and file-header chunks");
	if (stream_specific != (streamid_p != nullptr))
		throw std::invalid_argument("raw chunk stream-id presence does not match its tag");
	if (tag == chunk_tag_t::boundary && content.size() != 16)
		throw std::invalid_argument("boundary payload must contain exactly 16 bytes");
	if (tag == chunk_tag_t::clockoffset && content.size() != 16)
		throw std::invalid_argument("clock-offset payload must contain exactly 16 bytes");
	if (tag != chunk_tag_t::boundary && !stream_specific)
		throw std::invalid_argument("unsupported raw chunk tag");
	if ((tag == chunk_tag_t::streamheader || tag == chunk_tag_t::streamfooter ||
		 tag == chunk_tag_t::samples) && content.empty())
		throw std::invalid_argument("stream chunk payload must not be empty");
	std::lock_guard<std::mutex> lock(write_mut);
	_write_chunk(tag, content, streamid_p);
	if (!file_) throw std::runtime_error("could not append raw XDF chunk");
}

void XDFWriter::flush(bool durable) {
	std::lock_guard<std::mutex> lock(write_mut);
	file_.flush();
	if (!file_) throw std::runtime_error("could not flush XDF output");
	if (durable) output_->durable_sync();
}
