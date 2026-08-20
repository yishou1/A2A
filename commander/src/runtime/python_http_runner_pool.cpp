#include "algolib/runtime/python_http_runner_pool.h"

#include <chrono>
#include <memory>
#include <string>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/error_code.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/python_http_runner.h"

namespace algolib {

// ---------------------------------------------------------------------------
// 构造 / 析构
// ---------------------------------------------------------------------------

PythonHttpRunnerPool::PythonHttpRunnerPool(std::size_t pool_size, int checkout_timeout_ms)
    : pool_size_(pool_size > 0 ? pool_size : kDefaultPoolSize),
      checkout_timeout_ms_(checkout_timeout_ms > 0 ? checkout_timeout_ms
                                                    : kDefaultCheckoutTimeoutMs) {}

PythonHttpRunnerPool::~PythonHttpRunnerPool() {
    // 中文注释：析构时先置 draining_，唤醒所有等待线程，然后清理 runner。
    draining_.store(true, std::memory_order_release);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        cv_.notify_all();
        DrainLocked();
    }
    // 中文注释：all_runners_ 中的 unique_ptr 在此被销毁，各 runner 的析构函数清理连接。
}

// ---------------------------------------------------------------------------
// IAlgorithmRunner — Load
// ---------------------------------------------------------------------------

Status PythonHttpRunnerPool::Load(const AlgorithmEntry& entry) {
    if (entry.key.backend_type != BackendType::kPythonHttpService) {
        return Status::Error(
            ErrorCode::kBackendTypeMismatch,
            "PythonHttpRunnerPool can only load python_http_service entries.");
    }

    std::lock_guard<std::mutex> lock(mutex_);

    // 中文注释：若已经 ready，先做幂等检查（仅在 entry 相同时直接返回成功）。
    if (ready_.load(std::memory_order_relaxed)) {
        return Status::Ok();
    }

    entry_ = entry;
    draining_.store(false, std::memory_order_release);

    // 中文注释：创建 pool_size_ 个 PythonHttpRunner 并逐一调用 Load。
    // 若任何一个失败则整体回滚（清空 all_runners_ 和 idle_runners_）。
    all_runners_.reserve(pool_size_);
    for (std::size_t i = 0; i < pool_size_; ++i) {
        auto runner = std::make_unique<PythonHttpRunner>();
        Status load_status = runner->Load(entry);
        if (!load_status.ok()) {
            all_runners_.clear();
            idle_runners_.clear();
            return Status::Error(
                load_status.code(),
                "PythonHttpRunnerPool: runner[" + std::to_string(i) +
                    "] Load failed: " + load_status.message());
        }
        idle_runners_.push_back(runner.get());
        all_runners_.push_back(std::move(runner));
    }

    ready_.store(true, std::memory_order_release);
    return Status::Ok();
}

// ---------------------------------------------------------------------------
// IAlgorithmRunner — Unload
// ---------------------------------------------------------------------------

Status PythonHttpRunnerPool::Unload() {
    // 中文注释：设置 draining_，让所有阻塞在 Checkout 的线程立即收到通知并退出。
    draining_.store(true, std::memory_order_release);
    {
        std::lock_guard<std::mutex> lock(mutex_);
        cv_.notify_all();
        DrainLocked();
    }
    ready_.store(false, std::memory_order_release);
    draining_.store(false, std::memory_order_release);
    return Status::Ok();
}

// ---------------------------------------------------------------------------
// IAlgorithmRunner — Run
// ---------------------------------------------------------------------------

AlgorithmResult PythonHttpRunnerPool::Run(const AlgorithmRequest& request) {
    if (!ready_.load(std::memory_order_acquire)) {
        AlgorithmResult result;
        result.ok           = false;
        result.request_id   = request.request_id;
        result.trace_id     = request.trace_id;
        result.algorithm_id = request.algorithm_id;
        result.version      = request.version;
        result.backend_type = request.backend_type;
        result.outputs      = nlohmann::json::object();
        result.usage        = nlohmann::json::object();
        result.error = AlgorithmError{
            ToString(ErrorCode::kServiceUnavailable),
            "PythonHttpRunnerPool is not ready (not loaded)."
        };
        return result;
    }

    auto checkout_result = CheckoutRunner();
    if (!checkout_result.ok()) {
        AlgorithmResult result;
        result.ok           = false;
        result.request_id   = request.request_id;
        result.trace_id     = request.trace_id;
        result.algorithm_id = request.algorithm_id;
        result.version      = request.version;
        result.backend_type = request.backend_type;
        result.outputs      = nlohmann::json::object();
        result.usage        = nlohmann::json::object();
        result.error = AlgorithmError{
            ToString(checkout_result.status().code()),
            checkout_result.status().message()
        };
        return result;
    }

    // 中文注释：PooledRunnerGuard 析构时自动归还 runner，无论 Run 成功与否。
    auto guard = std::move(checkout_result.value());
    return guard.runner->Run(request);
}

// ---------------------------------------------------------------------------
// IAlgorithmRunner — HealthCheck
// ---------------------------------------------------------------------------

HealthStatus PythonHttpRunnerPool::HealthCheck() const {
    if (!ready_.load(std::memory_order_acquire)) {
        return HealthStatus{false, "unloaded", "PythonHttpRunnerPool has not been loaded."};
    }
    if (draining_.load(std::memory_order_acquire)) {
        return HealthStatus{false, "draining", "PythonHttpRunnerPool is draining (Unload in progress)."};
    }

    // 中文注释：从池中借出一个 runner 做健康检查后归还。
    // 若池全部繁忙且超时，返回 degraded（服务仍可能在处理请求，只是无空闲 runner 可检查）。
    auto checkout_result = const_cast<PythonHttpRunnerPool*>(this)->CheckoutRunner();
    if (!checkout_result.ok()) {
        return HealthStatus{
            false,
            "degraded",
            "All runners are busy during health check: " +
                checkout_result.status().message()
        };
    }

    auto guard = std::move(checkout_result.value());
    return guard.runner->HealthCheck();
}

// ---------------------------------------------------------------------------
// 池状态查询
// ---------------------------------------------------------------------------

std::size_t PythonHttpRunnerPool::idle_count() const {
    std::lock_guard<std::mutex> lock(mutex_);
    return idle_runners_.size();
}

// ---------------------------------------------------------------------------
// 内部实现
// ---------------------------------------------------------------------------

Result<PythonHttpRunnerPool::PooledRunnerGuard> PythonHttpRunnerPool::CheckoutRunner() {
    std::unique_lock<std::mutex> lock(mutex_);

    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(checkout_timeout_ms_);

    // 中文注释：等待直到有空闲 runner、池正在析构，或超时。
    const bool acquired = cv_.wait_until(lock, deadline, [this]() {
        return !idle_runners_.empty() || draining_.load(std::memory_order_relaxed);
    });

    if (draining_.load(std::memory_order_relaxed)) {
        return Status::Error(
            ErrorCode::kServiceUnavailable,
            "PythonHttpRunnerPool is draining; checkout rejected.");
    }

    if (!acquired || idle_runners_.empty()) {
        return Status::Error(
            ErrorCode::kServiceUnavailable,
            "PythonHttpRunnerPool: all " + std::to_string(pool_size_) +
                " runners are busy; checkout timed out after " +
                std::to_string(checkout_timeout_ms_) + " ms.");
    }

    // 中文注释：LIFO — 从队首取出（最近归还的 runner，连接热度最高）。
    IAlgorithmRunner* runner = idle_runners_.front();
    idle_runners_.pop_front();

    return PooledRunnerGuard{this, runner};
}

void PythonHttpRunnerPool::ReturnRunner(IAlgorithmRunner* runner) {
    if (!runner) return;
    {
        std::lock_guard<std::mutex> lock(mutex_);
        // 中文注释：归还时推回队首（LIFO）。
        idle_runners_.push_front(runner);
    }
    // 中文注释：通知一个等待线程，有 runner 可用了。
    cv_.notify_one();
}

void PythonHttpRunnerPool::DrainLocked() {
    // 中文注释：此函数在持有 mutex_ 的情况下调用。
    // 清空空闲队列，然后清空 all_runners_（unique_ptr 析构 → runner 析构 → 连接关闭）。
    idle_runners_.clear();
    all_runners_.clear();
}

}  // namespace algolib
