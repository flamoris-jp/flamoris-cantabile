#pragma once
#include <cstdint>
#include <stdexcept>
#include <string>
#include <vector>

namespace cantabile {
inline std::string base64_encode(const std::vector<std::uint8_t>& bytes) {
    static constexpr char alphabet[] = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    std::string out;
    out.reserve((bytes.size() + 2) / 3 * 4);
    for (std::size_t i = 0; i < bytes.size(); i += 3) {
        std::uint32_t x = static_cast<std::uint32_t>(bytes[i]) << 16;
        if (i + 1 < bytes.size()) x |= static_cast<std::uint32_t>(bytes[i + 1]) << 8;
        if (i + 2 < bytes.size()) x |= bytes[i + 2];
        out.push_back(alphabet[(x >> 18) & 63]);
        out.push_back(alphabet[(x >> 12) & 63]);
        out.push_back(i + 1 < bytes.size() ? alphabet[(x >> 6) & 63] : '=');
        out.push_back(i + 2 < bytes.size() ? alphabet[x & 63] : '=');
    }
    return out;
}

inline std::vector<std::uint8_t> base64_decode(const std::string& text, std::size_t limit = 16 * 1024 * 1024) {
    if (text.size() > ((limit + 2) / 3) * 4 || text.size() % 4 != 0)
        throw std::runtime_error("Invalid or oversized base64 input");
    auto decode = [](char c) -> int {
        if (c >= 'A' && c <= 'Z') return c - 'A';
        if (c >= 'a' && c <= 'z') return c - 'a' + 26;
        if (c >= '0' && c <= '9') return c - '0' + 52;
        if (c == '+') return 62;
        if (c == '/') return 63;
        return -1;
    };
    std::vector<std::uint8_t> out;
    out.reserve(text.size() / 4 * 3);
    for (std::size_t i = 0; i < text.size(); i += 4) {
        int a = decode(text[i]), b = decode(text[i + 1]);
        int c = text[i + 2] == '=' ? 0 : decode(text[i + 2]);
        int d = text[i + 3] == '=' ? 0 : decode(text[i + 3]);
        bool p2 = text[i + 2] == '=', p3 = text[i + 3] == '=';
        if (a < 0 || b < 0 || c < 0 || d < 0 || (p2 && !p3) ||
            ((p2 || p3) && i + 4 != text.size()) || (p2 && (b & 15)) || (!p2 && p3 && (c & 3)))
            throw std::runtime_error("Invalid base64 encoding or padding");
        auto x = static_cast<std::uint32_t>((a << 18) | (b << 12) | (c << 6) | d);
        out.push_back(static_cast<std::uint8_t>(x >> 16));
        if (!p2) out.push_back(static_cast<std::uint8_t>(x >> 8));
        if (!p3) out.push_back(static_cast<std::uint8_t>(x));
    }
    if (out.size() > limit) throw std::runtime_error("Decoded data exceeds limit");
    return out;
}
}
