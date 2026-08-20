#include <fstream>
#include <iostream>
#include <stdexcept>
#include <string>

#include <httplib.h>
#include <nlohmann/json.hpp>

namespace {

// 从磁盘读取完整 AlgorithmRequest JSON。
nlohmann::json ReadRequest(const std::string& request_path) {
    std::ifstream input(request_path, std::ios::in | std::ios::binary);
    if (!input.is_open()) {
        throw std::runtime_error("无法打开请求文件：" + request_path);
    }

    nlohmann::json request;
    input >> request;
    return request;
}

void PrintUsage() {
    std::cerr
        << "用法：\n"
        << "  algorithm_http_client <request_json> [server_host] [server_port]\n"
        << "示例：\n"
        << "  algorithm_http_client request.json 127.0.0.1 8088\n";
}

}  // namespace

int main(int argc, char* argv[]) {
    if (argc < 2 || argc > 4) {
        PrintUsage();
        return 1;
    }

    try {
        // 第一个参数是请求 JSON；主机和端口不传时默认连接本机 8088。
        const std::string request_path = argv[1];
        const std::string server_host = argc >= 3 ? argv[2] : "127.0.0.1";
        const int server_port = argc >= 4 ? std::stoi(argv[3]) : 8088;

        if (server_port <= 0 || server_port > 65535) {
            throw std::runtime_error("server_port 必须在 1 到 65535 之间。");
        }

        const nlohmann::json request = ReadRequest(request_path);

        // HTTP 客户端只连接算法库统一服务，不关心后端是 ONNX 还是 Python。
        httplib::Client client(server_host, server_port);
        client.set_connection_timeout(3, 0);
        client.set_read_timeout(30, 0);

        // 两类后端都调用同一个 POST /run 接口。
        const auto response = client.Post(
            "/run",
            request.dump(),
            "application/json");

        if (!response) {
            std::cerr << "无法连接算法库服务：http://"
                      << server_host << ':' << server_port << '\n';
            return 2;
        }

        std::cout << "HTTP " << response->status << '\n';

        // 格式化打印服务端返回的 JSON；非 JSON 响应则原样打印。
        const auto payload = nlohmann::json::parse(
            response->body,
            nullptr,
            false);
        if (payload.is_discarded()) {
            std::cout << response->body << '\n';
        } else {
            std::cout << payload.dump(2) << '\n';
        }

        const bool http_ok = response->status >= 200 && response->status < 300;
        const bool algorithm_ok =
            payload.is_object() && payload.value("ok", false);
        return http_ok && algorithm_ok ? 0 : 3;
    } catch (const std::exception& exception) {
        std::cerr << "客户端错误：" << exception.what() << '\n';
        return 1;
    }
}
