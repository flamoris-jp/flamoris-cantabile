#include <cantabile/core.h>
#include <cantabile/encoding.hpp>
#include <nlohmann/json.hpp>
#include <algorithm>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <vector>
#ifdef _WIN32
# include <windows.h>
#else
# include <unistd.h>
#endif

namespace {
using json = nlohmann::json;
namespace fs = std::filesystem;
constexpr std::size_t native_json_limit = 32 * 1024 * 1024;
// Saved projects must fit inside a later project.load request, including the
// operation, revision and budget envelope. Keep this reserve out of file state.
constexpr std::size_t file_project_limit = native_json_limit - 4096;
struct Destroy { void operator()(void* p) const { cantabile_destroy(p); } };
using Engine = std::unique_ptr<void, Destroy>;
json request(void* engine, const json& req) {
    auto bounded = req;
    if (bounded.is_object() && bounded.contains("op") && bounded["op"].is_string()) {
        const auto op = bounded["op"].get<std::string>();
        if (op == "project.load" || op == "project.new" || op == "midi.import" ||
            op == "edit.batch" || op == "history.undo" || op == "history.redo") {
            if (!bounded.contains("max_project_bytes")) bounded["max_project_bytes"] = file_project_limit;
            else {
                const auto& value = bounded["max_project_bytes"];
                // Preserve invalid budgets for native validation; valid callers
                // may make the budget smaller, never bypass the file reserve.
                if (value.is_number_unsigned()) {
                    auto budget = value.get<std::uint64_t>();
                    if (budget >= 1 && budget <= native_json_limit)
                        bounded["max_project_bytes"] = std::min<std::uint64_t>(budget, file_project_limit);
                } else if (value.is_number_integer()) {
                    auto budget = value.get<std::int64_t>();
                    if (budget >= 1 && budget <= static_cast<std::int64_t>(native_json_limit))
                        bounded["max_project_bytes"] = std::min<std::int64_t>(budget, file_project_limit);
                }
            }
        }
    }
    std::unique_ptr<char, decltype(&cantabile_free)> response(cantabile_request(engine, bounded.dump().c_str()), &cantabile_free);
    if (!response) throw std::runtime_error("Native request allocation failed");
    return json::parse(response.get());
}
json checked(void* engine, const json& req) {
    auto response = request(engine, req);
    if (!response.value("ok", false)) throw std::runtime_error(response.at("error").at("code").get<std::string>() + ": " + response.at("error").at("message").get<std::string>());
    return response;
}
std::vector<std::uint8_t> read_file(const fs::path& path, std::size_t limit = 32 * 1024 * 1024) {
    std::ifstream in(path, std::ios::binary | std::ios::ate);
    if (!in) throw std::runtime_error("Cannot open input file");
    auto end = in.tellg();
    if (end < 0 || static_cast<std::uint64_t>(end) > limit) throw std::runtime_error("Input file exceeds size limit");
    std::vector<std::uint8_t> bytes(static_cast<std::size_t>(end));
    in.seekg(0);
    if (!bytes.empty() && !in.read(reinterpret_cast<char*>(bytes.data()), static_cast<std::streamsize>(bytes.size())))
        throw std::runtime_error("Cannot read complete input file");
    return bytes;
}
json read_json(const fs::path& path) {
    auto bytes = read_file(path);
    return json::parse(bytes.begin(), bytes.end(), [](int depth, json::parse_event_t, json&) {
        if (depth > 64) throw std::runtime_error("JSON nesting exceeds 64 levels");
        return true;
    });
}
void write_atomic(const fs::path& destination, const std::vector<std::uint8_t>& bytes) {
    auto parent = destination.parent_path();
    if (parent.empty()) parent = ".";
    if (!fs::is_directory(parent)) throw std::runtime_error("Output directory does not exist");
    // Exclusive temporary creation also prevents one CLI process from overwriting
    // another process's temporary output. The final rename replaces atomically.
    fs::path temporary;
#ifdef _WIN32
    wchar_t name[MAX_PATH + 1];
    if (!GetTempFileNameW(parent.wstring().c_str(), L"cnt", 0, name)) throw std::runtime_error("Cannot create temporary output");
    temporary = name;
#else
    auto pattern = (parent / ("." + destination.filename().string() + ".XXXXXX")).string();
    std::vector<char> name(pattern.begin(), pattern.end()); name.push_back('\0');
    int fd = mkstemp(name.data());
    if (fd < 0) throw std::runtime_error("Cannot create temporary output");
    close(fd); temporary = name.data();
#endif
    try {
        {
            std::ofstream out(temporary, std::ios::binary | std::ios::trunc);
            if (!out) throw std::runtime_error("Cannot open temporary output");
            out.write(reinterpret_cast<const char*>(bytes.data()), static_cast<std::streamsize>(bytes.size()));
            out.flush();
            if (!out) throw std::runtime_error("Cannot write complete output");
        }
#ifdef _WIN32
        if (!MoveFileExW(temporary.wstring().c_str(), destination.wstring().c_str(), MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH))
            throw std::runtime_error("Cannot publish output");
#else
        fs::rename(temporary, destination);
#endif
    } catch (...) { std::error_code error; fs::remove(temporary, error); throw; }
}
void write_json(const fs::path& destination, const json& value) {
    // Persist the compact authoritative representation: pretty-printing opaque
    // byte arrays can otherwise inflate a valid project beyond the read limit.
    auto text = value.dump();
    if (text.size() > file_project_limit) throw std::runtime_error("Project exceeds the file CLI byte budget");
    write_atomic(destination, std::vector<std::uint8_t>(text.begin(), text.end()));
}
void help() {
    std::cout << "FLAMORIS Cantabile 0.1.0\n"
        "  cantabile-cli --stdio                     JSON-lines session, one response per request\n"
        "  cantabile-cli new PROJECT.json            Save an empty editable project\n"
        "  cantabile-cli inspect PROJECT.json        Validate and print the project\n"
        "  cantabile-cli validate PROJECT.json       Validate a saved project\n"
        "  cantabile-cli export-midi PROJECT OUT.mid Export Standard MIDI\n"
        "  cantabile-cli import-midi IN.mid PROJECT  Import Standard MIDI and save project\n"
        "  cantabile-cli edit PROJECT COMMANDS.json  Apply an atomic edit batch and save\n"
        "  cantabile-cli request REQUEST.json        Execute one request in a new session\n";
}
}

int main(int argc, char** argv) {
    try {
        if (argc < 2 || std::string(argv[1]) == "--help" || std::string(argv[1]) == "-h") { help(); return 0; }
        if (std::string(argv[1]) == "--version") { std::cout << "0.1.0\n"; return 0; }
        Engine engine(cantabile_create());
        if (!engine) throw std::runtime_error("Cannot create editing engine");
        std::string op = argv[1];
        if (op == "--stdio") {
            std::string line;
            // Read incrementally so an untrusted unterminated frame cannot grow
            // memory without bound. stdio here is the native JSON protocol, not MCP.
            bool too_large = false;
            char c;
            while (std::cin.get(c)) {
                if (c != '\n') {
                    if (!too_large && line.size() < 32 * 1024 * 1024) line.push_back(c);
                    else { too_large = true; line.clear(); }
                    continue;
                }
                if (too_large) {
                    std::cout << json({{"ok", false}, {"error", {{"code", "LIMIT_EXCEEDED"}, {"message", "Request exceeds 32 MiB"}}}}).dump() << '\n' << std::flush;
                } else if (!line.empty()) {
                    if (line.find('\0') != std::string::npos) {
                        std::cout << "{\"ok\":false,\"error\":{\"code\":\"INVALID_REQUEST\",\"message\":\"Unescaped NUL in JSON frame\"}}\n" << std::flush;
                        line.clear(); too_large = false;
                        continue;
                    }
                    std::unique_ptr<char, decltype(&cantabile_free)> response(cantabile_request(engine.get(), line.c_str()), &cantabile_free);
                    std::cout << (response ? response.get() : "{\"ok\":false,\"error\":{\"code\":\"ALLOCATION_FAILED\",\"message\":\"Native allocation failed\"}}") << '\n' << std::flush;
                }
                line.clear(); too_large = false;
            }
            if (too_large || !line.empty()) throw std::runtime_error("Incomplete JSON-lines frame: terminating newline is required");
            return 0;
        }
        if (op == "request" && argc == 3) {
            auto response = request(engine.get(), read_json(argv[2]));
            std::cout << response.dump(2) << '\n'; return response.value("ok", false) ? 0 : 1;
        }
        if (op == "new" && argc == 3) {
            if (fs::exists(argv[2])) throw std::runtime_error("A project already exists at the requested path");
            auto state = checked(engine.get(), {{"op", "project.get"}});
            write_json(argv[2], state["project"]); std::cout << state.dump() << '\n'; return 0;
        }
        if (op == "import-midi" && argc == 4) {
            auto bytes = read_file(argv[2], 16 * 1024 * 1024);
            auto response = checked(engine.get(), {{"op", "midi.import"}, {"expected_revision", 0}, {"data_base64", cantabile::base64_encode(bytes)}});
            write_json(argv[3], response["project"]); std::cout << response.dump() << '\n'; return 0;
        }
        if ((op == "inspect" || op == "validate") && argc == 3) {
            auto response = checked(engine.get(), {{"op", "project.load"}, {"expected_revision", 0}, {"data", read_json(argv[2])}});
            if (op == "validate") response.erase("project");
            std::cout << response.dump(2) << '\n'; return 0;
        }
        if ((op == "export-midi" || op == "edit") && argc == 4) {
            checked(engine.get(), {{"op", "project.load"}, {"expected_revision", 0}, {"data", read_json(argv[2])}});
            if (op == "export-midi") {
                auto response = checked(engine.get(), {{"op", "midi.export"}, {"expected_revision", 1}});
                auto bytes = cantabile::base64_decode(response.at("data_base64").get<std::string>());
                write_atomic(argv[3], bytes); response.erase("data_base64");
                std::cout << response.dump() << '\n';
            } else {
                auto commands = read_json(argv[3]);
                if (commands.is_object() && commands.contains("commands")) commands = commands["commands"];
                auto response = checked(engine.get(), {{"op", "edit.batch"}, {"expected_revision", 1}, {"commands", commands}});
                write_json(argv[2], response["project"]); std::cout << response.dump() << '\n';
            }
            return 0;
        }
        help(); return 2;
    } catch (const std::exception& e) {
        std::cerr << json({{"ok", false}, {"error", {{"code", "CLI_ERROR"}, {"message", e.what()}}}}).dump() << '\n';
        return 1;
    }
}
