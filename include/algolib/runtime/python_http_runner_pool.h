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

// 中文注释：PythonHttpRunnerPool — Python HTTP Service 并发连接池。
//
// 设计目标：
//   每个 PythonHttpRunner 内部持有一个 httplib::Client 实例，
//   而 httplib::Client 不是线程安全的，因此并发推理请求必须各自持有独立的 runner。
//   PythonHttpRunnerPool 维护固定数量的 PythonHttpRunner，并通过 Checkout/Return
//   语义安全地分发给并发请求。
//
// 并发模型：
//   - 空闲 runner 存放在 idle_runners_ 队列（LIFO，减少连接空闲时间）。
//   - 每次 Checkout 从队首取出；无空闲时阻塞，超时后返回错误。
//   - Return 将 runner 推回队首（可能触发等待线程唤醒）。
//   - Drain() 将所有空闲 runner 关闭并清空（用于 Unload/销毁场景）。
//
// 对外接口：
//   PythonHttpRunnerPool 继承 IAlgorithmRunner，Run/HealthCheck 内部透明地
//   完成 Checkout → 操作 → Return 的三步流程，让 ExecutionCoordinator 无需感知池化细节。
//
// 容量配置：
//   默认 pool_size = 4；可通过构造参数调整。
//   建议 pool_size ≤ Python 服务进程的 worker 线程数，以避免服务端排队。
class PythonHttpRunnerPool : public IAlgorithmRunner {
public:
    // 中文注释：kDefaultPoolSize — 默认池容量。
    static constexpr std::size_t kDefaultPoolSize = 4;

    // 中文注释：kDefaultCheckoutTimeoutMs — Checkout 最长等待时间（毫秒）。
    static constexpr int kDefaultCheckoutTimeoutMs = 5000;

    explicit PythonHttpRunnerPool(std::size_t pool_size = kDefaultPoolSize,
                                  int checkout_timeout_ms = kDefaultCheckoutTimeoutMs);

    // 禁止拷贝，允许移动（池本身不应复制）
    PythonHttpRunnerPool(const PythonHttpRunnerPool&) = delete;
    PythonHttpRunnerPool& operator=(const PythonHttpRunnerPool&) = delete;

    ~PythonHttpRunnerPool() override;

    // -----------------------------------------------------------------------
    // IAlgorithmRunner 接口实现
    // -----------------------------------------------------------------------

    // 中文注释：Load — 初始化池中所有 runner（创建 pool_size 个 PythonHttpRunner
    //           并逐一调用 Load，全部成功后池才变为 ready 状态）。
    Status Load(const AlgorithmEntry& entry) override;

    // 中文注释：Unload — 关闭所有连接并销毁池中 runner。
    Status Unload() override;

    // 中文注释：Run — 从池中 Checkout 一个 runner，执行推理，然后 Return。
    //           若 Checkout 超时，返回 kServiceUnavailable 错误。
    AlgorithmResult Run(const AlgorithmRequest& request) override;

    // 中文注释：HealthCheck — 从池中取出一个 runner 执行健康检查后归还。
    //           若池未就绪或全部繁忙，返回 degraded 状态。
    HealthStatus HealthCheck() const override;

    // -----------------------------------------------------------------------
    // 池状态查询（仅用于测试/监控）
    // -----------------------------------------------------------------------

    // 中文注释：idle_count — 当前空闲 runner 数量（近似值，不加锁）。
    std::size_t idle_count() const;

    // 中文注释：pool_size — 池容量（初始化后固定）。
    std::size_t pool_size() const { return pool_size_; }

    // 中文注释：is_ready — 池是否已完成 Load 初始化。
    bool is_ready() const { return ready_.load(std::memory_order_acquire); }

private:
    // -----------------------------------------------------------------------
    // 内部辅助
    // -----------------------------------------------------------------------

    // 中文注释：RAII guard — 自动将 runner 归还给池。
    // 由 CheckoutRunner 返回，析构时调用 Return。
    struct PooledRunnerGuard {
        PythonHttpRunnerPool* pool = nullptr;
        IAlgorithmRunner* runner = nullptr;

        PooledRunnerGuard() = default;
        PooledRunnerGuard(PythonHttpRunnerPool* p, IAlgorithmRunner* r)
            : pool(p), runner(r) {}
        ~PooledRunnerGuard() {
            if (pool && runner) {
                pool->ReturnRunner(runner);
            }
        }

        // 禁止拷贝
        PooledRunnerGuard(const PooledRunnerGuard&) = delete;
        PooledRunnerGuard& operator=(const PooledRunnerGuard&) = delete;

        // 允许移动（转移归还责任）
        PooledRunnerGuard(PooledRunnerGuard&& other) noexcept
            : pool(other.pool), runner(other.runner) {
            other.pool = nullptr;
            other.runner = nullptr;
        }
    };

    // 中文注释：CheckoutRunner — 从空闲队列取出一个 runner（阻塞，带超时）。
    // 返回 nullptr 表示超时或池未就绪。
    Result<PooledRunnerGuard> CheckoutRunner();

    // 中文注释：ReturnRunner — 将 runner 归还到空闲队列，并通知一个等待线程。
    void ReturnRunner(IAlgorithmRunner* runner);

    // 中文注释：DrainLocked — 在持有锁的情况下销毁所有空闲 runner。
    void DrainLocked();

    // -----------------------------------------------------------------------
    // 成员变量
    // -----------------------------------------------------------------------

    const std::size_t pool_size_;
    const int checkout_timeout_ms_;

    // 中文注释：所有 runner 的所有权（全量列表，Load 时创建，Unload 时销毁）。
    std::vector<std::unique_ptr<IAlgorithmRunner>> all_runners_;

    // 中文注释：当前空闲的 runner 指针队列（仅借用裸指针，所有权在 all_runners_）。
    std::deque<IAlgorithmRunner*> idle_runners_;

    mutable std::mutex mutex_;
    std::condition_variable cv_;

    // 中文注释：ready_ — Load 成功后设为 true，Unload 后置回 false。
    std::atomic<bool> ready_{false};

    // 中文注释：draining_ — Unload 进行中，阻止新 Checkout 进入等待。
    std::atomic<bool> draining_{false};

    // 中文注释：entry_ — 保存 Load 时传入的算法条目，供恢复 checkout 超时后重建使用。
    AlgorithmEntry entry_;
};

}  // namespace algolib
