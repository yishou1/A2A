#include "algolib/io/sha256.h"

#include <array>
#include <cstdint>
#include <iomanip>
#include <sstream>
#include <string>
#include <vector>

namespace algolib {
namespace {

constexpr std::array<std::uint32_t, 64> kRoundConstants = {
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1,
    0x923f82a4, 0xab1c5ed5, 0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3,
    0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174, 0xe49b69c1, 0xefbe4786,
    0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147,
    0x06ca6351, 0x14292967, 0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13,
    0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85, 0xa2bfe8a1, 0xa81a664b,
    0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a,
    0x5b9cca4f, 0x682e6ff3, 0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208,
    0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
};

constexpr std::array<std::uint32_t, 8> kInitialState = {
    0x6a09e667,
    0xbb67ae85,
    0x3c6ef372,
    0xa54ff53a,
    0x510e527f,
    0x9b05688c,
    0x1f83d9ab,
    0x5be0cd19,
};

std::uint32_t RotateRight(std::uint32_t value, std::uint32_t bits) {
    return (value >> bits) | (value << (32U - bits));
}

std::uint32_t Choose(std::uint32_t x, std::uint32_t y, std::uint32_t z) {
    return (x & y) ^ (~x & z);
}

std::uint32_t Majority(std::uint32_t x, std::uint32_t y, std::uint32_t z) {
    return (x & y) ^ (x & z) ^ (y & z);
}

std::uint32_t BigSigma0(std::uint32_t value) {
    return RotateRight(value, 2U) ^ RotateRight(value, 13U) ^ RotateRight(value, 22U);
}

std::uint32_t BigSigma1(std::uint32_t value) {
    return RotateRight(value, 6U) ^ RotateRight(value, 11U) ^ RotateRight(value, 25U);
}

std::uint32_t SmallSigma0(std::uint32_t value) {
    return RotateRight(value, 7U) ^ RotateRight(value, 18U) ^ (value >> 3U);
}

std::uint32_t SmallSigma1(std::uint32_t value) {
    return RotateRight(value, 17U) ^ RotateRight(value, 19U) ^ (value >> 10U);
}

std::vector<std::uint8_t> PadMessage(const std::string& input) {
    std::vector<std::uint8_t> bytes(input.begin(), input.end());
    const std::uint64_t bit_length = static_cast<std::uint64_t>(bytes.size()) * 8ULL;

    bytes.push_back(0x80U);
    while ((bytes.size() % 64U) != 56U) {
        bytes.push_back(0x00U);
    }

    for (int shift = 56; shift >= 0; shift -= 8) {
        bytes.push_back(static_cast<std::uint8_t>((bit_length >> shift) & 0xffULL));
    }

    return bytes;
}

}  // namespace

std::string ComputeSha256Hex(const std::string& input) {
    std::array<std::uint32_t, 8> state = kInitialState;
    const std::vector<std::uint8_t> padded = PadMessage(input);

    for (std::size_t offset = 0; offset < padded.size(); offset += 64U) {
        std::array<std::uint32_t, 64> schedule{};
        for (std::size_t index = 0; index < 16U; ++index) {
            const std::size_t byte_index = offset + index * 4U;
            schedule[index] =
                (static_cast<std::uint32_t>(padded[byte_index]) << 24U) |
                (static_cast<std::uint32_t>(padded[byte_index + 1]) << 16U) |
                (static_cast<std::uint32_t>(padded[byte_index + 2]) << 8U) |
                static_cast<std::uint32_t>(padded[byte_index + 3]);
        }

        for (std::size_t index = 16U; index < 64U; ++index) {
            schedule[index] = SmallSigma1(schedule[index - 2U]) + schedule[index - 7U] +
                              SmallSigma0(schedule[index - 15U]) + schedule[index - 16U];
        }

        std::uint32_t a = state[0];
        std::uint32_t b = state[1];
        std::uint32_t c = state[2];
        std::uint32_t d = state[3];
        std::uint32_t e = state[4];
        std::uint32_t f = state[5];
        std::uint32_t g = state[6];
        std::uint32_t h = state[7];

        for (std::size_t index = 0; index < 64U; ++index) {
            const std::uint32_t temp1 =
                h + BigSigma1(e) + Choose(e, f, g) + kRoundConstants[index] + schedule[index];
            const std::uint32_t temp2 = BigSigma0(a) + Majority(a, b, c);

            h = g;
            g = f;
            f = e;
            e = d + temp1;
            d = c;
            c = b;
            b = a;
            a = temp1 + temp2;
        }

        state[0] += a;
        state[1] += b;
        state[2] += c;
        state[3] += d;
        state[4] += e;
        state[5] += f;
        state[6] += g;
        state[7] += h;
    }

    std::ostringstream output;
    output << std::hex << std::setfill('0');
    for (std::uint32_t word : state) {
        output << std::setw(8) << word;
    }
    return output.str();
}

}  // namespace algolib
