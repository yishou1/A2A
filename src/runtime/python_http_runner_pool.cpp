#include "algolib/runtime/python_http_runner_pool.h"

#include <chrono>
#include <memory>
#include <string>
#include <utility>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/error_code.h"
#include "algolib/runtime/algorithm_request.h"
#include "algolib/runtime/algorithm_result.h"
#include "algolib/runtime/python_http_runner.h"

namespace algolib {

struct PythonHttpRunnerPool::PoolState {
    mutable std::mutex mutex;
    std::condition_variable cv;
    std::vector<std::shared_ptr<IAlgorithmRunner>> all_runners;
    std::deque<std::shared_ptr<IAlgorithmRunner>> idle_runners;
    std::size_t borrowed_count = 0;
    bool ready = false;
    bool draining = false;
    bool owner_alive = true;
};

PythonHttpRunnerPool::PythonHttpRunnerPool(std::size_t pool_size, int checkout_timeout_ms)
    : pool_size_(pool_size > 0 ? pool_size : kDefaultPoolSize),
      checkout_timeout_ms_(checkout_timeout_ms > 0 ? checkout_timeout_ms
                                                    : kDefaultCheckoutTimeoutMs),
      state_(std::make_shared<PoolState>()) {}

PythonHttpRunnerPool::~PythonHttpRunnerPool() {
    // 析构不等待网络 I/O；已借出的 lease 自带 state/runner 所有权，归还后安全释放。
    const auto state = state_;
    if (!state) {
        return;
    }
    {
        std::lock_guard<std::mutex> lock(state->mutex);
        state->owner_alive = false;
        state->draining = true;
        state->ready = false;
        state->idle_runners.clear();
        FinalizeDrainLocked(state.get());
    }
    ready_.store(false, std::memory_order_release);
    draining_.store(true, std::memory_order_release);
    state->cv.notify_all();
}

PythonHttpRunnerPool::PooledRunnerGuard::~PooledRunnerGuard() {
    if (state && runner) {
        PythonHttpRunnerPool::ReturnRunner(state, runner);
    }
}

Status PythonHttpRunnerPool::Load(const AlgorithmEntry& entry) {
    if (entry.key.backend_type != BackendType::kPythonHttpService) {
        return Status::Error(
            ErrorCode::kBackendTypeMismatch,
            "PythonHttpRunnerPool can only load python_http_service entries.");
    }

    const auto state = state_;
    if (!state) {
        return Status::Error(ErrorCode::kServiceUnavailable,
                             "PythonHttpRunnerPool state is unavailable.");
    }

    std::lock_guard<std::mutex> lock(state->mutex);
    if (!state->owner_alive) {
        return Status::Error(ErrorCode::kServiceUnavailable,
                             "PythonHttpRunnerPool has been destroyed.");
    }
    if (state->ready) {
        return Status::Ok();
    }
    if (state->borrowed_count != 0) {
        return Status::Error(ErrorCode::kServiceUnavailable,
                             "PythonHttpRunnerPool is still draining previous runners.");
    }

    state->all_runners.clear();
    state->idle_runners.clear();
    state->all_runners.reserve(pool_size_);
    state->draining = false;

    std::vector<std::shared_ptr<IAlgorithmRunner>> new_runners;
    new_runners.reserve(pool_size_);
    for (std::size_t i = 0; i < pool_size_; ++i) {
        auto runner = std::make_shared<PythonHttpRunner>();
        const Status load_status = runner->Load(entry);
        if (!load_status.ok()) {
            return Status::Error(
                load_status.code(),
                "PythonHttpRunnerPool: runner[" + std::to_string(i) +
                    "] Load failed: " + load_status.message());
        }
        new_runners.push_back(std::move(runner));
    }

    state->all_runners = new_runners;
    for (const auto& runner : new_runners) {
        state->idle_runners.push_back(runner);
    }
    state->ready = true;
    state->draining = false;
    entry_ = entry;
    ready_.store(true, std::memory_order_release);
    draining_.store(false, std::memory_order_release);
    state->cv.notify_all();
    return Status::Ok();
}

Status PythonHttpRunnerPool::Unload() {
    const auto state = state_;
    if (!state) {
        return Status::Ok();
    }

    {
        std::lock_guard<std::mutex> lock(state->mutex);
        // 先拒绝新租约；all_runners 仅在最后一个已借租约归还后释放。
        state->ready = false;
        state->draining = true;
        state->idle_runners.clear();
        FinalizeDrainLocked(state.get());
    }
    ready_.store(false, std::memory_order_release);
    draining_.store(true, std::memory_order_release);
    state->cv.notify_all();
    return Status::Ok();
}

AlgorithmResult PythonHttpRunnerPool::Run(const AlgorithmRequest& request) {
    if (!ready_.load(std::memory_order_acquire)) {
        return BuildUnavailableResult(
            request,
            Status::Error(ErrorCode::kServiceUnavailable,
                          "PythonHttpRunnerPool is not ready (not loaded)."));
    }

    auto checkout_result = CheckoutRunner();
    if (!checkout_result.ok()) {
        return BuildUnavailableResult(request, checkout_result.status());
    }

    auto guard = std::move(checkout_result.value());
    return guard.runner->Run(request);
}

HealthStatus PythonHttpRunnerPool::HealthCheck() const {
    if (!ready_.load(std::memory_order_acquire)) {
        return HealthStatus{false, "unloaded", "PythonHttpRunnerPool has not been loaded."};
    }
    if (draining_.load(std::memory_order_acquire)) {
        return HealthStatus{false, "draining", "PythonHttpRunnerPool is draining."};
    }

    auto checkout_result = const_cast<PythonHttpRunnerPool*>(this)->CheckoutRunner();
    if (!checkout_result.ok()) {
        return HealthStatus{
            false,
            "degraded",
            "All runners are busy during health check: " +
                checkout_result.status().message(),
        };
    }

    auto guard = std::move(checkout_result.value());
    return guard.runner->HealthCheck();
}

std::size_t PythonHttpRunnerPool::idle_count() const {
    const auto state = state_;
    if (!state) {
        return 0;
    }
    std::lock_guard<std::mutex> lock(state->mutex);
    return state->idle_runners.size();
}

Result<PythonHttpRunnerPool::PooledRunnerGuard> PythonHttpRunnerPool::CheckoutRunner() {
    const auto state = state_;
    if (!state) {
        return Status::Error(ErrorCode::kServiceUnavailable,
                             "PythonHttpRunnerPool state is unavailable.");
    }

    std::unique_lock<std::mutex> lock(state->mutex);
    const auto deadline = std::chrono::steady_clock::now() +
                          std::chrono::milliseconds(checkout_timeout_ms_);
    const bool acquired = state->cv.wait_until(lock, deadline, [&state]() {
        return !state->idle_runners.empty() || !state->ready || state->draining ||
               !state->owner_alive;
    });

    if (!state->owner_alive || state->draining || !state->ready) {
        return Status::Error(ErrorCode::kServiceUnavailable,
                             "PythonHttpRunnerPool is draining; checkout rejected.");
    }
    if (!acquired || state->idle_runners.empty()) {
        return Status::Error(
            ErrorCode::kServiceUnavailable,
            "PythonHttpRunnerPool: all " + std::to_string(pool_size_) +
                " runners are busy; checkout timed out after " +
                std::to_string(checkout_timeout_ms_) + " ms.");
    }

    auto runner = state->idle_runners.front();
    state->idle_runners.pop_front();
    ++state->borrowed_count;
    return PooledRunnerGuard{state, std::move(runner)};
}

void PythonHttpRunnerPool::ReturnRunner(const std::shared_ptr<PoolState>& state,
                                        const std::shared_ptr<IAlgorithmRunner>& runner) {
    if (!state || !runner) {
        return;
    }

    {
        std::lock_guard<std::mutex> lock(state->mutex);
        if (state->borrowed_count > 0) {
            --state->borrowed_count;
        }
        if (state->owner_alive && state->ready && !state->draining) {
            // LIFO：优先复用最近使用的 runner。
            state->idle_runners.push_front(runner);
        }
        FinalizeDrainLocked(state.get());
    }
    state->cv.notify_all();
}

void PythonHttpRunnerPool::FinalizeDrainLocked(PoolState* state) {
    if (!state || !state->draining || state->borrowed_count != 0) {
        return;
    }
    state->idle_runners.clear();
    state->all_runners.clear();
}

AlgorithmResult PythonHttpRunnerPool::BuildUnavailableResult(
    const AlgorithmRequest& request, const Status& status) const {
    AlgorithmResult result;
    result.ok = false;
    result.request_id = request.request_id;
    result.trace_id = request.trace_id;
    result.algorithm_id = request.algorithm_id;
    result.version = request.version;
    result.backend_type = request.backend_type;
    result.outputs = nlohmann::json::object();
    result.usage = nlohmann::json::object();
    result.error = AlgorithmError{ToString(status.code()), status.message()};
    return result;
}

}  // namespace algolib
