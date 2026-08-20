#pragma once

#include <optional>
#include <stdexcept>
#include <string>
#include <utility>

#include "algolib/core/error_code.h"

namespace algolib {

class Status {  // 中文注释: Status 用来表达函数执行结果 避免在控制流里滥用异常
public:
    static Status Ok() {
        return Status(true, ErrorCode::kOk, "");
    }

    static Status Error(ErrorCode code, std::string message) {
        return Status(false, code, std::move(message));
    }

    bool ok() const {
        return ok_;
    }

    ErrorCode code() const {
        return code_;
    }

    const std::string& message() const {
        return message_;
    }

    std::string ToString() const {
        if (ok_) {
            return "OK";
        }
        return algolib::ToString(code_) + ": " + message_;
    }

private:
    Status(bool ok, ErrorCode code, std::string message)
        : ok_(ok), code_(code), message_(std::move(message)) {}

    bool ok_ = true;
    ErrorCode code_ = ErrorCode::kOk;
    std::string message_;
};

template <typename T>
class Result {  // 中文注释: Result<T> 用于同时返回状态和值 调用侧可以显式检查错误
public:
    Result(const T& value) : status_(Status::Ok()), value_(value) {}
    Result(T&& value) : status_(Status::Ok()), value_(std::move(value)) {}
    Result(const Status& status) : status_(status) {}
    Result(Status&& status) : status_(std::move(status)) {}

    bool ok() const {
        return status_.ok();
    }

    const Status& status() const {
        return status_;
    }

    const T& value() const {
        if (!value_.has_value()) {
            throw std::logic_error("Result does not contain a value.");
        }
        return *value_;
    }

    T& value() {
        if (!value_.has_value()) {
            throw std::logic_error("Result does not contain a value.");
        }
        return *value_;
    }

private:
    Status status_ = Status::Ok();
    std::optional<T> value_;
};

}  // namespace algolib
