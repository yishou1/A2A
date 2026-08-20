#include <cstdlib>
#include <filesystem>
#include <iostream>
#include <stdexcept>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

#include "algolib/io/json_utils.h"
#include "algolib/server/http_server.h"

namespace {

std::filesystem::path ResolveRegistryPath() {
    if (const char* env_value = std::getenv("ALGOLIB_REGISTRY_PATH");
        env_value != nullptr && *env_value != '\0') {
        return std::filesystem::path(env_value);
    }
    return std::filesystem::current_path() / ".algolib" / "registry.json";
}

std::filesystem::path ResolveExecutionLogPath() {
    if (const char* env_value = std::getenv("ALGOLIB_EXECUTION_LOG_PATH");
        env_value != nullptr && *env_value != '\0') {
        return std::filesystem::path(env_value);
    }
    return {};
}

std::string ResolveHost() {
    if (const char* env_value = std::getenv("ALGOLIB_SERVER_HOST");
        env_value != nullptr && *env_value != '\0') {
        return env_value;
    }
    return "127.0.0.1";
}

int ResolvePort() {
    if (const char* env_value = std::getenv("ALGOLIB_SERVER_PORT");
        env_value != nullptr && *env_value != '\0') {
        return std::stoi(env_value);
    }
    return 8088;
}

void PrintUsage() {
    std::cout << "algolib_server [--host <host>] [--port <port>] "
              << "[--registry <registry_json_path>] "
              << "[--execution-log <audit_jsonl_path>]\n";
}

void PrintError(const std::string& message) {
    nlohmann::json payload{
        {"ok", false},
        {"error_code", "INVALID_ARGUMENT"},
        {"message", message},
    };
    std::cerr << algolib::JsonUtils::Pretty(payload) << std::endl;
}

algolib::HttpServerConfig ParseConfigOrThrow(const std::vector<std::string>& args) {
    algolib::HttpServerConfig config;
    config.registry_path = ResolveRegistryPath();
    config.execution_log_path = ResolveExecutionLogPath();
    config.host = ResolveHost();
    config.port = ResolvePort();

    for (std::size_t i = 0; i < args.size(); ++i) {
        const std::string& arg = args[i];
        if (arg == "--help" || arg == "-h") {
            PrintUsage();
            std::exit(0);
        }

        if (i + 1 >= args.size()) {
            throw std::runtime_error("Missing value for option: " + arg);
        }

        const std::string& value = args[++i];
        if (arg == "--host") {
            config.host = value;
        } else if (arg == "--port") {
            config.port = std::stoi(value);
        } else if (arg == "--registry") {
            config.registry_path = value;
        } else if (arg == "--execution-log") {
            config.execution_log_path = value;
        } else {
            throw std::runtime_error("Unknown option: " + arg);
        }
    }

    if (config.port <= 0 || config.port > 65535) {
        throw std::runtime_error("Port must be between 1 and 65535.");
    }
    if (config.host.empty()) {
        throw std::runtime_error("Host must be non-empty.");
    }
    return config;
}

}  // namespace

int main(int argc, char* argv[]) {
    try {
        const std::vector<std::string> args(argv + 1, argv + argc);
        algolib::HttpServerConfig config = ParseConfigOrThrow(args);

        // 中文注释：默认只监听 127.0.0.1，显式传 --host 0.0.0.0 时才允许局域网访问。
        algolib::AlgolibHttpServer server(config);
        std::cout << "algolib_server listening on http://" << config.host << ':'
                  << config.port << '\n'
                  << "registry: " << config.registry_path.generic_string() << '\n'
                  << "press Ctrl+C to stop\n";

        if (!server.Listen()) {
            PrintError("Failed to start algolib_server. Check registry path and port binding.");
            return 1;
        }
        return 0;
    } catch (const std::exception& ex) {
        PrintError(ex.what());
        PrintUsage();
        return 1;
    }
}
