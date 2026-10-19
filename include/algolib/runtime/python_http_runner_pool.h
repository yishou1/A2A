#pragma once

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <cstddef>
#include <deque>
#include <memory>
#include <mutex>
#include <string>
#include <vector>

#include "algolib/core/algorithm_entry.h"
#include "algolib/core/status.h"
#include "algolib/runtime/algorithm_runner.h"

namespace algolib {

// 中文注释：PythonHttpRunnerPool — Python HTTP Service 并发 Runner 池。
//
// 生命周期语义：
//   - Checkout 返回 shared_ptr 租约，已借出的 runner 在 Unload/析构期间仍保持存活。
//   - Unload 切换到 draining，拒绝新借出；最后一个租约归还后才真正释放 runner。
//   - 因此缓存失效、/unload 与在途推理可以安全并发，不会产生裸指针 UAF。
class PythonHttpRunnerPool : public IAlgorithmRunner {
public:
    static constexpr std::size_t kDefaultPoolSize = 8;
    static constexpr int kDefaultCheckoutTimeoutMs = 5000;

    explicit PythonHttpRunnerPool(std::size_t pool_size = kDefaultPoolSize,
                                  int checkout_timeout_ms = kDefaultCheckoutTimeoutMs);

    PythonHttpRunnerPool(const PythonHttpRunnerPool&) = delete;
    PythonHttpRunnerPool& operator=(const PythonHttpRunnerPool&) = delete;

    ~PythonHttpRunnerPool() override;

    Status Load(const AlgorithmEntry& entry) override;
    Status Unload() override;
    AlgorithmResult Run(const AlgorithmRequest& request) override;
    HealthStatus HealthCheck() const override;

    std::size_t idle_count() const;
    std::size_t pool_size() const { return pool_size_; }
    bool is_ready() const { return ready_.load(std::memory_order_acquire); }

private:
    struct PoolState;

    // RAII lease: runner 的 shared_ptr 和状态 shared_ptr 均独立持有，Pool 本体销毁后
    // 也不会访问悬空 this 指针。
    struct PooledRunnerGuard {
        std::shared_ptr<PoolState> state;
        std::shared_ptr<IAlgorithmRunner> runner;

        PooledRunnerGuard() = default;
        PooledRunnerGuard(std::shared_ptr<PoolState> state_value,
                          std::shared_ptr<IAlgorithmRunner> runner_value)
            : state(std::move(state_value)), runner(std::move(runner_value)) {}
        ~PooledRunnerGuard();

        PooledRunnerGuard(const PooledRunnerGuard&) = delete;
        PooledRunnerGuard& operator=(const PooledRunnerGuard&) = delete;
        PooledRunnerGuard(PooledRunnerGuard&& other) noexcept
            : state(std::move(other.state)), runner(std::move(other.runner)) {}
        PooledRunnerGuard& operator=(PooledRunnerGuard&& other) {
            if (this != &other) {
                if (state && runner) {
                    PythonHttpRunnerPool::ReturnRunner(state, runner);
                }
                state = std::move(other.state);
                runner = std::move(other.runner);
            }
            return *this;
        }
    };

    Result<PooledRunnerGuard> CheckoutRunner();
    static void ReturnRunner(const std::shared_ptr<PoolState>& state,
                             const std::shared_ptr<IAlgorithmRunner>& runner);
    static void FinalizeDrainLocked(PoolState* state);
    AlgorithmResult BuildUnavailableResult(const AlgorithmRequest& request,
                                           const Status& status) const;

    const std::size_t pool_size_;
    const int checkout_timeout_ms_;
    std::shared_ptr<PoolState> state_;
    std::atomic<bool> ready_{false};
    std::atomic<bool> draining_{false};
    AlgorithmEntry entry_;
};

}  // namespace algolib
