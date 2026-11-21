// SPDX-License-Identifier: Apache-2.0
#pragma once

#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <stdexcept>
#include <string>
#include <utility>

#ifndef _WIN32
#include <sys/stat.h>
#endif

namespace cantabile::audio {
namespace fs = std::filesystem;

struct Error : std::runtime_error {
    std::string code;
    Error(std::string c, const std::string& message) : std::runtime_error(message), code(std::move(c)) {}
};
[[noreturn]] inline void fail(const std::string& code, const std::string& message) { throw Error(code, message); }

struct WaveFile {
    fs::path temporary, destination;
    std::FILE* file = nullptr;
    bool published = false;
    explicit WaveFile(const fs::path& output) : destination(output) {
        std::error_code ec;
        if (fs::exists(output, ec)) fail("OUTPUT_EXISTS", "Output already exists");
        auto parent = output.parent_path();
        if (parent.empty()) parent = ".";
        if (!fs::is_directory(parent, ec)) fail("OUTPUT_FAILED", "Output parent directory does not exist");
        // The job-owning host knows this exact name and can remove it after
        // a forced process kill. Exclusive creation never adopts another job's
        // already-existing staging file.
        temporary = output;
        temporary += ".part";
#ifdef _WIN32
        file = _wfopen(temporary.c_str(), L"wbx");
#else
        file = std::fopen(temporary.c_str(), "wbx");
#endif
        if (!file) fail("OUTPUT_FAILED", "Cannot create temporary audio file");
#ifndef _WIN32
        if (::fchmod(::fileno(file), S_IRUSR | S_IWUSR)) {
            std::fclose(file); file = nullptr; fs::remove(temporary, ec);
            fail("OUTPUT_FAILED", "Cannot restrict temporary audio permissions");
        }
#endif
    }
    WaveFile(const WaveFile&) = delete;
    WaveFile& operator=(const WaveFile&) = delete;

    ~WaveFile() {
        if (file) std::fclose(file);
        if (!published) { std::error_code ignored; fs::remove(temporary, ignored); }
    }
    void bytes(const void* data, std::size_t size) {
        if (std::fwrite(data, 1, size, file) != size) fail("OUTPUT_FAILED", "Audio write failed");
    }
    void little(std::uint32_t value, unsigned count) {
        unsigned char b[4];
        for (unsigned i = 0; i < count; ++i) b[i] = static_cast<unsigned char>(value >> (8 * i));
        bytes(b, count);
    }
    void header(std::uint32_t frames, int rate, bool floating = false) {
        auto alignment = floating ? 8U : 4U;
        auto data_size = frames * alignment;
        bytes("RIFF", 4); little(data_size + 36, 4); bytes("WAVEfmt ", 8);
        little(16, 4); little(floating ? 3 : 1, 2); little(2, 2); little(static_cast<std::uint32_t>(rate), 4);
        little(static_cast<std::uint32_t>(rate * alignment), 4); little(alignment, 2); little(floating ? 32 : 16, 2);
        bytes("data", 4); little(data_size, 4);
    }
    void publish() {
        if (std::fflush(file) || std::ferror(file)) fail("OUTPUT_FAILED", "Cannot flush audio file");
        auto handle = file; file = nullptr;
        if (std::fclose(handle)) fail("OUTPUT_FAILED", "Cannot close audio file");
        std::error_code ec;
        // Same-directory hard-link creation is atomic and never overwrites an
        // existing output, including one created after the initial existence check.
        fs::create_hard_link(temporary, destination, ec);
        if (ec) fail("OUTPUT_FAILED", "Cannot publish audio file without overwriting an existing output");
        fs::remove(temporary, ec);
        published = true;
    }
};

} // namespace cantabile::audio
