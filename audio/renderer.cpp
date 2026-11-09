// SPDX-License-Identifier: Apache-2.0
// Offline SoundFont renderer. No MIDI/audio device or GUI is created.
#include <algorithm>
#include <array>
#include <cerrno>
#include <cmath>
#include <csignal>
#include <cstdint>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <vector>

#ifdef _WIN32
#define NOMINMAX
#include <windows.h>
#else
#include <dlfcn.h>
#include <sys/stat.h>
#endif

namespace fs = std::filesystem;
namespace {
volatile std::sig_atomic_t interrupted = 0;
void signal_handler(int) { interrupted = 1; }

struct Error : std::runtime_error {
    std::string code;
    Error(std::string c, const std::string& message) : std::runtime_error(message), code(std::move(c)) {}
};
[[noreturn]] void fail(const std::string& code, const std::string& message) { throw Error(code, message); }

std::string quoted(const std::string& value) {
    std::string result = "\"";
    const char* hex = "0123456789abcdef";
    for (unsigned char c : value) {
        if (c == '"' || c == '\\') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 32) { result += "\\u00"; result += hex[c >> 4]; result += hex[c & 15]; }
        else result += static_cast<char>(c);
    }
    return result + '"';
}

struct Options {
    fs::path midi, font, output;
    int sample_rate = 48000;
    double tail = 2.0, maximum = 600.0;
    std::string backend = "fluidsynth";
};
double number(const std::string& value, const std::string& flag) {
    try {
        std::size_t used = 0;
        double result = std::stod(value, &used);
        if (used != value.size() || !std::isfinite(result)) throw std::invalid_argument("number");
        return result;
    } catch (...) { fail("INVALID_ARGUMENT", "Invalid numeric value for " + flag); }
}
Options arguments(int argc, char** argv) {
    Options o;
    for (int i = 1; i < argc; ++i) {
        std::string key = argv[i];
        if (key == "--help") {
            std::cout << "cantabile-render --midi FILE --soundfont FILE --output FILE.wav\n"
                         "  [--sample-rate 48000] [--tail-seconds 2] [--max-seconds 600]\n"
                         "  [--backend fluidsynth]\n";
            std::exit(0);
        }
        if (i + 1 == argc) fail("INVALID_ARGUMENT", "Missing value for " + key);
        std::string value = argv[++i];
        if (key == "--midi") o.midi = value;
        else if (key == "--soundfont") o.font = value;
        else if (key == "--output") o.output = value;
        else if (key == "--backend") o.backend = value;
        else if (key == "--sample-rate") {
            double rate = number(value, key);
            if (rate < 8000 || rate > 192000 || std::floor(rate) != rate)
                fail("INVALID_ARGUMENT", "sample-rate must be an integer from 8000 to 192000");
            o.sample_rate = static_cast<int>(rate);
        } else if (key == "--tail-seconds") o.tail = number(value, key);
        else if (key == "--max-seconds") o.maximum = number(value, key);
        else fail("INVALID_ARGUMENT", "Unknown option: " + key);
    }
    if (o.backend != "fluidsynth") fail("UNSUPPORTED_BACKEND", "Only the fluidsynth backend is implemented; VST 3 hosting is pending");
    if (o.midi.empty() || o.font.empty() || o.output.empty())
        fail("INVALID_ARGUMENT", "--midi, --soundfont and --output are required");
    if (o.tail < 0 || o.tail > 30 || o.maximum <= 0 || o.maximum > 3600)
        fail("INVALID_ARGUMENT", "tail-seconds must be 0..30 and max-seconds must be >0..3600");
    return o;
}

std::vector<std::uint8_t> read_midi(const fs::path& path) {
    std::error_code ec;
    if (!fs::is_regular_file(path, ec)) fail("INPUT_NOT_FOUND", "MIDI input is not a regular file");
    auto size = fs::file_size(path, ec);
    if (ec || size < 14 || size > 16 * 1024 * 1024)
        fail("INVALID_MIDI", "MIDI input size must be 14 bytes to 16 MiB");
    std::ifstream stream(path, std::ios::binary);
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(size));
    if (!stream.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size())))
        fail("INPUT_READ_FAILED", "Cannot read complete MIDI input");
    return bytes;
}

// An independent bounded SMF validator also computes the musical endpoint. This
// prevents a malformed or very long MIDI file from controlling the render loop.
struct Reader {
    const std::vector<std::uint8_t>& data;
    std::size_t position = 0, end;
    std::uint8_t byte() {
        if (position >= end) fail("INVALID_MIDI", "Truncated MIDI event");
        return data[position++];
    }
    std::uint32_t big(unsigned count) {
        std::uint32_t value = 0;
        while (count--) value = (value << 8) | byte();
        return value;
    }
    std::uint32_t vlq() {
        std::uint32_t value = 0;
        for (int i = 0; i < 4; ++i) {
            auto b = byte(); value = (value << 7) | (b & 127);
            if (!(b & 128)) return value;
        }
        fail("INVALID_MIDI", "MIDI variable-length quantity exceeds four bytes");
    }
    void skip(std::size_t count) {
        if (count > end - position) fail("INVALID_MIDI", "MIDI payload exceeds its track");
        position += count;
    }
};
struct Tempo { std::uint64_t tick; std::uint32_t microseconds; std::size_t order; };

double midi_duration(const std::vector<std::uint8_t>& bytes) {
    Reader file{bytes, 0, bytes.size()};
    if (file.big(4) != 0x4d546864) fail("INVALID_MIDI", "Missing MThd header");
    auto header_size = file.big(4);
    if (header_size < 6 || header_size > bytes.size() - 8) fail("INVALID_MIDI", "Invalid MThd length");
    auto format = file.big(2), tracks = file.big(2), ppq = file.big(2);
    if (format > 1 || (ppq & 0x8000)) fail("UNSUPPORTED_MIDI", "Renderer supports SMF Type 0/1 with PPQ timing");
    if (!tracks || tracks > 256 || (format == 0 && tracks != 1) || !ppq)
        fail("INVALID_MIDI", "Invalid MIDI track count or PPQ");
    file.skip(header_size - 6);
    std::vector<Tempo> tempos;
    std::uint64_t last_tick = 0;
    std::size_t event_count = 0;
    for (std::uint32_t index = 0; index < tracks; ++index) {
        if (file.big(4) != 0x4d54726b) fail("INVALID_MIDI", "Missing MTrk chunk");
        auto length = file.big(4);
        if (length > file.end - file.position) fail("INVALID_MIDI", "Truncated MTrk chunk");
        Reader track{bytes, file.position, file.position + length};
        file.skip(length);
        std::uint64_t tick = 0;
        std::uint8_t running = 0;
        bool eot = false;
        while (track.position < track.end) {
            if (++event_count > 1000000) fail("INVALID_MIDI", "MIDI event limit exceeded");
            tick += track.vlq();
            if (tick > std::numeric_limits<std::int32_t>::max()) fail("DURATION_LIMIT", "MIDI tick range exceeds renderer limit");
            last_tick = std::max(last_tick, tick);
            auto status = track.byte();
            if (status < 0x80) {
                if (!running) fail("INVALID_MIDI", "Running status has no channel status");
                --track.position; status = running;
            }
            if (status >= 0x80 && status <= 0xef) {
                running = status;
                int count = (status >> 4) == 0xc || (status >> 4) == 0xd ? 1 : 2;
                while (count--) if (track.byte() >= 128) fail("INVALID_MIDI", "Channel data byte has status bit set");
            } else if (status == 0xff) {
                running = 0;
                auto type = track.byte(); auto size = track.vlq();
                if (type >= 128) fail("INVALID_MIDI", "Invalid MIDI meta-event type");
                if (type == 0x2f) {
                    if (size != 0 || track.position != track.end) fail("INVALID_MIDI", "End-of-track must be the last zero-length event");
                    eot = true;
                } else if (type == 0x51) {
                    if (size != 3) fail("INVALID_MIDI", "Tempo must contain three bytes");
                    auto us = track.big(3);
                    if (!us) fail("INVALID_MIDI", "MIDI tempo must be positive");
                    tempos.push_back({tick, us, event_count});
                } else track.skip(size);
            } else if (status == 0xf0 || status == 0xf7) {
                running = 0; track.skip(track.vlq());
            } else fail("INVALID_MIDI", "Unsupported SMF status byte");
        }
        if (!eot) fail("INVALID_MIDI", "Missing end-of-track event");
    }
    if (file.position != file.end) fail("INVALID_MIDI", "Unexpected data after final MIDI track");
    std::stable_sort(tempos.begin(), tempos.end(), [](const Tempo& a, const Tempo& b) {
        return a.tick < b.tick || (a.tick == b.tick && a.order < b.order);
    });
    std::uint64_t tick = 0;
    std::uint32_t tempo = 500000;
    long double microseconds = 0;
    for (const auto& change : tempos) {
        microseconds += static_cast<long double>(change.tick - tick) * tempo / ppq;
        tick = change.tick; tempo = change.microseconds;
    }
    microseconds += static_cast<long double>(last_tick - tick) * tempo / ppq;
    return static_cast<double>(microseconds / 1000000);
}

// Runtime loading keeps this worker optional for the editing engine and permits
// a clear BACKEND_UNAVAILABLE error without vendoring FluidSynth or its headers.
struct Fluid {
#ifdef _WIN32
    HMODULE module = nullptr;
#else
    void* module = nullptr;
#endif
    using ptr = void*;
    ptr (*settings_new)() = nullptr;
    void (*settings_delete)(ptr) = nullptr;
    int (*setstr)(ptr, const char*, const char*) = nullptr;
    int (*setnum)(ptr, const char*, double) = nullptr;
    int (*setint)(ptr, const char*, int) = nullptr;
    ptr (*synth_new)(ptr) = nullptr;
    void (*synth_delete)(ptr) = nullptr;
    int (*sfload)(ptr, const char*, int) = nullptr;
    ptr (*player_new)(ptr) = nullptr;
    void (*player_delete)(ptr) = nullptr;
    int (*player_add)(ptr, const void*, std::size_t) = nullptr;
    int (*player_play)(ptr) = nullptr;
    int (*player_stop)(ptr) = nullptr;
    int (*player_join)(ptr) = nullptr;
    int (*write_float)(ptr, int, void*, int, int, void*, int, int) = nullptr;
    template<class T> void symbol(T& target, const char* name) {
#ifdef _WIN32
        target = reinterpret_cast<T>(GetProcAddress(module, name));
#else
        target = reinterpret_cast<T>(dlsym(module, name));
#endif
        if (!target) fail("BACKEND_UNAVAILABLE", std::string("Missing FluidSynth symbol: ") + name);
    }
    Fluid() {
#ifdef _WIN32
        module = LoadLibraryExW(L"libfluidsynth-3.dll", nullptr, LOAD_LIBRARY_SEARCH_DEFAULT_DIRS);
#elif defined(__APPLE__)
        module = dlopen("libfluidsynth.3.dylib", RTLD_NOW | RTLD_LOCAL);
#else
        module = dlopen("libfluidsynth.so.3", RTLD_NOW | RTLD_LOCAL);
#endif
        if (!module) fail("BACKEND_UNAVAILABLE", "FluidSynth 2.x runtime library could not be loaded");
        try {
            symbol(settings_new, "new_fluid_settings"); symbol(settings_delete, "delete_fluid_settings");
            symbol(setstr, "fluid_settings_setstr"); symbol(setnum, "fluid_settings_setnum");
            symbol(setint, "fluid_settings_setint"); symbol(synth_new, "new_fluid_synth");
            symbol(synth_delete, "delete_fluid_synth"); symbol(sfload, "fluid_synth_sfload");
            symbol(player_new, "new_fluid_player"); symbol(player_delete, "delete_fluid_player");
            symbol(player_add, "fluid_player_add_mem"); symbol(player_play, "fluid_player_play");
            symbol(player_stop, "fluid_player_stop"); symbol(player_join, "fluid_player_join");
            symbol(write_float, "fluid_synth_write_float");
        } catch (...) { close(); throw; }
    }
    void close() {
        if (!module) return;
#ifdef _WIN32
        FreeLibrary(module);
#else
        dlclose(module);
#endif
        module = nullptr;
    }
    // This executable handles one job and then exits. FluidSynth's dependency
    // stack may retain process-wide background state (e.g. SoundFont loaders),
    // so do not dlclose the runtime before process exit. Individual settings,
    // synth and player objects are still explicitly destroyed by Session.
    ~Fluid() = default;
};
struct Session {
    Fluid& api;
    void* settings = nullptr; void* synth = nullptr; void* player = nullptr;
    explicit Session(Fluid& fluid) : api(fluid) {}
    ~Session() {
        if (player) { api.player_stop(player); api.player_join(player); api.player_delete(player); }
        if (synth) api.synth_delete(synth);
        if (settings) api.settings_delete(settings);
    }
};

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
        file = std::fopen(temporary.string().c_str(), "wbx");
        if (!file) fail("OUTPUT_FAILED", "Cannot create temporary audio file");
#ifndef _WIN32
        if (::fchmod(::fileno(file), S_IRUSR | S_IWUSR)) {
            std::fclose(file); file = nullptr; fs::remove(temporary, ec);
            fail("OUTPUT_FAILED", "Cannot restrict temporary audio permissions");
        }
#endif
    }
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
    void header(std::uint32_t frames, int rate) {
        auto data_size = frames * 4;
        bytes("RIFF", 4); little(data_size + 36, 4); bytes("WAVEfmt ", 8);
        little(16, 4); little(1, 2); little(2, 2); little(static_cast<std::uint32_t>(rate), 4);
        little(static_cast<std::uint32_t>(rate * 4), 4); little(4, 2); little(16, 2);
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

int render(const Options& o) {
    const auto midi = read_midi(o.midi);
    const auto music_seconds = midi_duration(midi);
    const auto total_seconds = music_seconds + o.tail;
    if (!std::isfinite(total_seconds) || total_seconds > o.maximum)
        fail("DURATION_LIMIT", "MIDI duration plus tail exceeds max-seconds");
    const auto frame_count = static_cast<std::uint64_t>(std::ceil(total_seconds * o.sample_rate));
    if (frame_count == 0 || frame_count > (std::numeric_limits<std::uint32_t>::max() - 36) / 4)
        fail("DURATION_LIMIT", "Output exceeds WAV PCM frame limits or is empty");
    std::error_code ec;
    if (!fs::is_regular_file(o.font, ec)) fail("SOUNDFONT_NOT_FOUND", "SoundFont is not a regular file");
    if (fs::file_size(o.font, ec) > 1024ull * 1024 * 1024 || ec)
        fail("SOUNDFONT_LIMIT", "SoundFont must be at most 1 GiB");
    // Validate/create the private output before starting native MIDI playback.
    // An existing output must fail without loading a sound bank or starting a
    // player that has not yet advanced its sample clock.
    WaveFile output(o.output);
    output.header(static_cast<std::uint32_t>(frame_count), o.sample_rate);
    Fluid api;
    Session session(api);
    session.settings = api.settings_new();
    if (!session.settings) fail("BACKEND_FAILED", "Cannot create FluidSynth settings");
    auto settings_ok = api.setstr(session.settings, "player.timing-source", "sample") >= 0
        && api.setnum(session.settings, "synth.sample-rate", o.sample_rate) >= 0
        && api.setint(session.settings, "synth.lock-memory", 0) >= 0
        && api.setint(session.settings, "synth.cpu-cores", 1) >= 0
        && api.setint(session.settings, "synth.polyphony", 256) >= 0
        && api.setint(session.settings, "synth.reverb.active", 0) >= 0
        && api.setint(session.settings, "synth.chorus.active", 0) >= 0;
    if (!settings_ok) fail("BACKEND_FAILED", "Cannot configure FluidSynth for offline audio");
    session.synth = api.synth_new(session.settings);
    if (!session.synth) fail("BACKEND_FAILED", "Cannot create FluidSynth synthesizer");
    if (api.sfload(session.synth, o.font.string().c_str(), 1) < 0)
        fail("SOUNDFONT_INVALID", "FluidSynth could not load the SoundFont");
    session.player = api.player_new(session.synth);
    if (!session.player || api.player_add(session.player, midi.data(), midi.size()) < 0)
        fail("BACKEND_FAILED", "FluidSynth could not load the validated MIDI");
    if (api.player_play(session.player) < 0) fail("BACKEND_FAILED", "Cannot start offline MIDI playback");
    constexpr int block_size = 1024;
    std::array<float, block_size> left{}, right{};
    std::array<unsigned char, block_size * 4> pcm{};
    std::uint64_t clipped = 0;
    double peak = 0;
    for (std::uint64_t position = 0; position < frame_count;) {
        int count = static_cast<int>(std::min<std::uint64_t>(block_size, frame_count - position));
        if (api.write_float(session.synth, count, left.data(), 0, 1, right.data(), 0, 1) < 0)
            fail("BACKEND_FAILED", "FluidSynth audio processing failed");
        // FluidSynth initializes its sample-clock player lazily at the first
        // processing block. Let that initialization finish before cancellation
        // tears down the player, including a signal received while loading SF2.
        if (interrupted) fail("CANCELLED", "Audio rendering was cancelled");
        for (int i = 0; i < count; ++i) {
            for (int channel = 0; channel < 2; ++channel) {
                double sample = channel == 0 ? left[i] : right[i];
                if (!std::isfinite(sample)) fail("INVALID_AUDIO", "SoundFont produced a non-finite audio sample");
                peak = std::max(peak, std::abs(sample));
                if (sample > 1 || sample < -1) ++clipped;
                auto value = static_cast<std::int16_t>(std::lrint(std::clamp(sample, -1.0, 1.0) * 32767));
                auto offset = static_cast<std::size_t>(i * 4 + channel * 2);
                pcm[offset] = static_cast<unsigned char>(value);
                pcm[offset + 1] = static_cast<unsigned char>(static_cast<std::uint16_t>(value) >> 8);
            }
        }
        output.bytes(pcm.data(), static_cast<std::size_t>(count * 4));
        position += static_cast<unsigned>(count);
    }
    if (interrupted) fail("CANCELLED", "Audio rendering was cancelled");
    output.publish();
    std::cout << std::setprecision(12) << "{\"ok\":true,\"backend\":\"fluidsynth\",\"output\":" << quoted(o.output.string())
        << ",\"sample_rate\":" << o.sample_rate << ",\"channels\":2,\"sample_format\":\"pcm_s16le\",\"frames\":" << frame_count
        << ",\"duration_seconds\":" << static_cast<double>(frame_count) / o.sample_rate
        << ",\"music_seconds\":" << music_seconds << ",\"peak\":" << peak << ",\"clipped_samples\":" << clipped << "}\n";
    return 0;
}
} // namespace

int main(int argc, char** argv) {
    std::signal(SIGINT, signal_handler);
    std::signal(SIGTERM, signal_handler);
    try { return render(arguments(argc, argv)); }
    catch (const Error& error) {
        std::cout << "{\"ok\":false,\"error\":{\"code\":" << quoted(error.code) << ",\"message\":" << quoted(error.what()) << "}}\n";
        return error.code == "CANCELLED" ? 130 : 1;
    } catch (const std::exception& error) {
        std::cout << "{\"ok\":false,\"error\":{\"code\":\"RENDER_FAILED\",\"message\":" << quoted(error.what()) << "}}\n";
        return 1;
    }
}
