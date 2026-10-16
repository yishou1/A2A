"""
GPU并行实验调度器
支持4张GPU并行运行多个实验配置
"""

import subprocess
import threading
import queue
import time
import os
import sys
from datetime import datetime
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from scripts.experiments.exp_configs import ABLATION_CONFIGS, DATASETS, get_config_command


class GPUWorker(threading.Thread):
    """单个GPU工作线程"""

    def __init__(self, gpu_id, task_queue, result_queue):
        super().__init__()
        self.gpu_id = gpu_id
        self.task_queue = task_queue
        self.result_queue = result_queue
        self.daemon = True

    def run(self):
        while True:
            try:
                task = self.task_queue.get(timeout=1)
                if task is None:  # 结束信号
                    break

                config_name, dataset = task
                self.run_experiment(config_name, dataset)
                self.task_queue.task_done()

            except queue.Empty:
                continue
            except Exception as e:
                print(f"[GPU {self.gpu_id}] 错误: {e}")
                self.result_queue.put({
                    'config': config_name,
                    'dataset': dataset,
                    'status': 'failed',
                    'error': str(e)
                })
                self.task_queue.task_done()

    def run_experiment(self, config_name, dataset):
        """在指定GPU上运行单个实验"""
        config = ABLATION_CONFIGS[config_name]
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # 日志文件
        log_file = f"experiments/ablation_mroaw_agentic/logs/exp_{config_name}_{dataset}_{timestamp}.log"

        # 生成命令 - 使用 main_ablation.py 而不是 main.py
        cmd_args = get_config_command(config_name, dataset)
        cmd = f"CUDA_VISIBLE_DEVICES={self.gpu_id} python main_ablation.py {cmd_args}"

        print(f"\n{'='*80}")
        print(f"[GPU {self.gpu_id}] 开始实验")
        print(f"  配置: {config['short_name']}")
        print(f"  数据集: {dataset}")
        print(f"  日志: {log_file}")
        print(f"  时间: {timestamp}")
        print(f"{'='*80}\n")

        start_time = time.time()

        try:
            # 运行实验，输出重定向到日志文件
            with open(log_file, 'w') as log_f:
                log_f.write(f"命令: {cmd}\n")
                log_f.write(f"开始时间: {timestamp}\n")
                log_f.write("="*80 + "\n\n")
                log_f.flush()

                process = subprocess.Popen(
                    cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                # 实时输出并写入日志
                for line in process.stdout:
                    print(f"[GPU {self.gpu_id}|{config_name[:12]}] {line}", end='')
                    log_f.write(line)
                    log_f.flush()

                process.wait()

                if process.returncode != 0:
                    raise RuntimeError(f"实验失败，返回码: {process.returncode}")

            duration = time.time() - start_time
            print(f"\n[GPU {self.gpu_id}] ✅ 完成: {config_name} @ {dataset} ({duration/60:.1f}分钟)")

            self.result_queue.put({
                'config': config_name,
                'dataset': dataset,
                'status': 'success',
                'duration': duration,
                'log_file': log_file
            })

        except Exception as e:
            duration = time.time() - start_time
            print(f"\n[GPU {self.gpu_id}] ❌ 失败: {config_name} @ {dataset}")
            print(f"  错误: {e}")

            self.result_queue.put({
                'config': config_name,
                'dataset': dataset,
                'status': 'failed',
                'error': str(e),
                'duration': duration,
                'log_file': log_file
            })


class ParallelExperimentRunner:
    """并行实验管理器"""

    def __init__(self, gpu_ids, configs=None, datasets=None):
        """
        Args:
            gpu_ids: GPU ID列表，如 [0, 1, 2, 3]
            configs: 要运行的配置列表，默认为全部
            datasets: 要运行的数据集列表，默认为全部
        """
        self.gpu_ids = gpu_ids
        self.configs = configs or list(ABLATION_CONFIGS.keys())
        self.datasets = datasets or DATASETS

        self.task_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.workers = []

    def prepare_tasks(self):
        """准备任务队列"""
        tasks = []
        for config in self.configs:
            for dataset in self.datasets:
                tasks.append((config, dataset))

        # 将任务加入队列
        for task in tasks:
            self.task_queue.put(task)

        print(f"\n📊 实验计划:")
        print(f"  配置数: {len(self.configs)}")
        print(f"  数据集数: {len(self.datasets)}")
        print(f"  总任务数: {len(tasks)}")
        print(f"  并行GPU数: {len(self.gpu_ids)}")
        print(f"  GPU IDs: {self.gpu_ids}\n")

        return len(tasks)

    def run(self):
        """启动并行实验"""
        total_tasks = self.prepare_tasks()

        # 启动GPU工作线程
        for gpu_id in self.gpu_ids:
            worker = GPUWorker(gpu_id, self.task_queue, self.result_queue)
            worker.start()
            self.workers.append(worker)

        print(f"🚀 已启动 {len(self.workers)} 个GPU工作线程\n")

        # 等待所有任务完成
        self.task_queue.join()

        # 发送结束信号
        for _ in self.workers:
            self.task_queue.put(None)

        # 等待所有线程结束
        for worker in self.workers:
            worker.join()

        # 收集结果
        results = []
        while not self.result_queue.empty():
            results.append(self.result_queue.get())

        return results

    def print_summary(self, results):
        """打印实验总结"""
        success_count = sum(1 for r in results if r['status'] == 'success')
        failed_count = len(results) - success_count
        total_time = sum(r.get('duration', 0) for r in results)

        print("\n" + "="*80)
        print("📊 实验总结")
        print("="*80)
        print(f"  总任务数: {len(results)}")
        print(f"  ✅ 成功: {success_count}")
        print(f"  ❌ 失败: {failed_count}")
        print(f"  ⏱️  总耗时: {total_time/3600:.2f} 小时")
        print(f"  ⚡ 平均单任务: {total_time/len(results)/60:.1f} 分钟")
        print("="*80)

        if failed_count > 0:
            print("\n失败任务详情:")
            for r in results:
                if r['status'] == 'failed':
                    print(f"  ❌ {r['config']} @ {r['dataset']}")
                    print(f"     错误: {r.get('error', 'Unknown')}")
                    print(f"     日志: {r.get('log_file', 'N/A')}")

        print("\n")


def main():
    """主函数"""
    import argparse

    parser = argparse.ArgumentParser(description='并行运行消融实验')
    parser.add_argument('--gpus', type=str, default='0,1,2,3',
                        help='GPU IDs (逗号分隔), 例如: 0,1,2,3')
    parser.add_argument('--configs', type=str, default=None,
                        help='配置列表 (逗号分隔), 默认全部')
    parser.add_argument('--datasets', type=str, default=None,
                        help='数据集列表 (逗号分隔), 默认全部')

    args = parser.parse_args()

    # 解析GPU IDs
    gpu_ids = [int(x.strip()) for x in args.gpus.split(',')]

    # 解析配置
    configs = None
    if args.configs:
        configs = [x.strip() for x in args.configs.split(',')]
        # 验证配置名称
        invalid = set(configs) - set(ABLATION_CONFIGS.keys())
        if invalid:
            print(f"❌ 无效配置: {invalid}")
            print(f"   可用配置: {list(ABLATION_CONFIGS.keys())}")
            return

    # 解析数据集
    datasets = None
    if args.datasets:
        datasets = [x.strip() for x in args.datasets.split(',')]

    # 创建运行器并执行
    runner = ParallelExperimentRunner(gpu_ids, configs, datasets)

    print("\n" + "="*80)
    print("🚀 概率化MROAW + Agentic PPR 消融实验")
    print("="*80)

    start_time = time.time()
    results = runner.run()
    total_time = time.time() - start_time

    # 打印总结
    runner.print_summary(results)

    # 保存结果元数据
    import json
    metadata_file = f"experiments/ablation_mroaw_agentic/metadata_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(metadata_file, 'w') as f:
        json.dump({
            'total_time': total_time,
            'gpu_ids': gpu_ids,
            'configs': configs or list(ABLATION_CONFIGS.keys()),
            'datasets': datasets or DATASETS,
            'results': results
        }, f, indent=2)

    print(f"📄 元数据已保存: {metadata_file}\n")


if __name__ == '__main__':
    main()
